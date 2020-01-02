from functools import total_ordering
import os.path
import re
import sys
from typing import (TYPE_CHECKING, Any, Callable, Dict, Iterable, List,
                    NamedTuple, Optional, Set, Tuple, Type, Union)

from kitty.boss import Boss                       # type: ignore
from kitty.cli import Namespace, parse_args       # type: ignore
from kitty.conf.definition import (               # type: ignore
    Option, config_lines, option_func)
from kitty.conf.utils import (                    # type: ignore
    init_config, key_func, load_config, merge_dicts, parse_config_base,
    parse_kittens_key, resolve_config, to_color)
from kitty.constants import config_dir            # type: ignore
from kitty.fast_data_types import (               # type: ignore
    set_clipboard_string, truncate_point_for_length, wcswidth)
import kitty.key_encoding as kk                   # type: ignore
from kitty.key_encoding import KeyEvent           # type: ignore
from kitty.rgb import color_as_sgr                # type: ignore
from kittens.tui.handler import Handler           # type: ignore
from kittens.tui.loop import Loop                 # type: ignore


if TYPE_CHECKING:
    from typing_extensions import TypedDict
    ResultDict = TypedDict('ResultDict', {'copy': str})


AbsoluteLine = int
ScreenLine = int
ScreenColumn = int
SelectionInLine = Union[Tuple[ScreenColumn, ScreenColumn],
                        Tuple[None, None]]


PositionBase = NamedTuple('Position', [
    ('x', ScreenColumn), ('y', ScreenLine), ('top_line', AbsoluteLine)])
class Position(PositionBase):
    """
    Coordinates of a cell.

    :param x: 0-based, left of window, to the right
    :param y: 0-based, top of window, down
    :param top_line: 1-based, start of scrollback, down
    """
    @property
    def line(self) -> AbsoluteLine:
        """
        Return 1-based absolute line number.
        """
        return self.y + self.top_line

    def moved(self, dx: int = 0, dy: int = 0,
              dtop: int = 0) -> 'Position':
        """
        Return a new position specified relative to self.
        """
        return self._replace(x=self.x + dx, y=self.y + dy,
                             top_line=self.top_line + dtop)

    def __str__(self) -> str:
        return '{},{}+{}'.format(self.x, self.y, self.top_line)

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, Position):
            return NotImplemented
        return (self.line, self.x) < (other.line, other.x)

    def __le__(self, other: Any) -> bool:
        if not isinstance(other, Position):
            return NotImplemented
        return (self.line, self.x) <= (other.line, other.x)

    def __gt__(self, other: Any) -> bool:
        if not isinstance(other, Position):
            return NotImplemented
        return (self.line, self.x) > (other.line, other.x)

    def __ge__(self, other: Any) -> bool:
        if not isinstance(other, Position):
            return NotImplemented
        return (self.line, self.x) >= (other.line, other.x)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Position):
            return NotImplemented
        return (self.line, self.x) == (other.line, other.x)

    def __ne__(self, other: Any) -> bool:
        if not isinstance(other, Position):
            return NotImplemented
        return (self.line, self.x) != (other.line, other.x)



class Region:
    name = None  # type: Optional[str]
    uses_mark = False

    @staticmethod
    def line_inside_region(current_line: AbsoluteLine,
                           start: Position, end: Position) -> bool:
        """
        Return True if current_line is entirely inside the region
        defined by start and end.
        """
        return False

    @staticmethod
    def line_outside_region(current_line: AbsoluteLine,
                            start: Position, end: Position) -> bool:
        """
        Return True if current_line is entirely outside the region
        defined by start and end.
        """
        return current_line < start.line or end.line < current_line

    @staticmethod
    def adjust(start: Position, end: Position) -> Tuple[Position, Position]:
        """
        Return the normalized pair of markers
        equivalent to start and end. This is region-type-specific.
        """
        return start, end

    @staticmethod
    def selection_in_line(
            current_line: int, start: Position, end: Position,
            maxx: int) -> SelectionInLine:
        """
        Return bounds of the part of current_line
        that are within the region defined by start and end.
        """
        return None, None

    @staticmethod
    def lines_affected(start: Position, end: Position, line: AbsoluteLine,
                       dx: int, dy: int) -> Set[AbsoluteLine]:
        """
        Return the set of lines (1-based, top of scrollback, down)
        that must be redrawn when point that is on `line` moves by dx, dy.
        """
        return set()


class NoRegion(Region):
    name = 'unselected'
    uses_mark = False

    @staticmethod
    def line_outside_region(current_line: AbsoluteLine,
                            start: Position, end: Position) -> bool:
        return False


class StreamRegion(Region):
    name = 'stream'
    uses_mark = True

    @staticmethod
    def line_inside_region(current_line: AbsoluteLine,
                           start: Position, end: Position) -> bool:
        return start.line < current_line < end.line

    @staticmethod
    def selection_in_line(
            current_line: AbsoluteLine, start: Position, end: Position,
            maxx: ScreenColumn) -> SelectionInLine:
        if StreamRegion.line_outside_region(current_line, start, end):
            return None, None
        return (start.x if current_line == start.line else 0,
                end.x if current_line == end.line else maxx)

    @staticmethod
    def lines_affected(start: Position, end: Position, line: AbsoluteLine,
                       dx: int, dy: int) -> Set[AbsoluteLine]:
        return {line, line - dy}


class ColumnarRegion(Region):
    name = 'columnar'
    uses_mark = True

    @staticmethod
    def adjust(start: Position, end: Position) -> Tuple[Position, Position]:
        return (start._replace(x=min(start.x, end.x)),
                end._replace(x=max(start.x, end.x)))

    @staticmethod
    def selection_in_line(
            current_line: AbsoluteLine, start: Position, end: Position,
            maxx: ScreenColumn) -> SelectionInLine:
        if ColumnarRegion.line_outside_region(current_line, start, end):
            return None, None
        return start.x, end.x

    @staticmethod
    def lines_affected(start: Position, end: Position, line: AbsoluteLine,
                       dx: int, dy: int) -> Set[AbsoluteLine]:
        return (set(range(start.line, end.line + 1)) if dx
                else {line, line - dy})


Options = Any  # dynamically created namespace class
OptionName = str
OptionValues = Dict[OptionName, Any]
OptionDefs = Dict[OptionName, Option]


def parse_opts() -> Options:
    all_options = {}  # type: OptionDefs
    o, k, g, _all_groups = option_func(all_options, {
        'shortcuts': ['Keyboard shortcuts'],
        'colors': ['Colors']
    })

    g('shortcuts')
    k('quit', 'q', 'quit')
    k('quit', 'esc', 'quit')
    k('confirm', 'enter', 'confirm')
    k('left', 'left', 'move left')
    k('right', 'right', 'move right')
    k('up', 'up', 'move up')
    k('down', 'down', 'move down')
    k('scroll up', 'ctrl+up', 'scroll up')
    k('scroll down', 'ctrl+down', 'scroll down')
    k('select left', 'shift+left', 'select stream left')
    k('select right', 'shift+right', 'select stream right')
    k('select up', 'shift+up', 'select stream up')
    k('select down', 'shift+down', 'select stream down')
    k('column select left', 'alt+left', 'select columnar left')
    k('column select right', 'alt+right', 'select columnar right')
    k('column select up', 'alt+up', 'select columnar up')
    k('column select down', 'alt+down', 'select columnar down')

    g('colors')
    o('selection_foreground', '#FFFFFF', option_type=to_color)
    o('selection_background', '#5294E2', option_type=to_color)

    type_map = {o.name: o.option_type
                for o in all_options.values()
                if hasattr(o, 'option_type')}

    defaults = None

    # Parsers/validators for key binding directives
    func_with_args, args_funcs = key_func()

    @func_with_args('move')
    def move(func: Callable, direction: str) -> Tuple[Callable, str]:
        assert direction.lower() in ['left', 'right', 'up', 'down']
        return func, direction.lower()

    @func_with_args('scroll')
    def scroll(func: Callable, direction: str) -> Tuple[Callable, str]:
        assert direction.lower() in ['up', 'down']
        return func, direction.lower()

    @func_with_args('select')
    def select(func: Callable, args: str) -> Tuple[Callable, Tuple[str, str]]:
        region_type, direction = args.split(' ', 1)
        assert region_type.lower() in ['stream', 'columnar']
        assert direction.lower() in ['left', 'right', 'up', 'down']
        return func, (region_type.lower(), direction.lower())

    # Configuration reader helpers
    def special_handling(key: OptionName, val: str,
                         result: OptionValues) -> bool:
        if key == 'map':
            action, *key_def = parse_kittens_key(val, args_funcs)
            result['key_definitions'][tuple(key_def)] = action
            return True
        return False

    def parse_config(lines: List[str],
                     check_keys: bool = True) -> OptionValues:
        result = {'key_definitions': {}}  # type: OptionValues
        parse_config_base(lines, defaults, type_map, special_handling,
                          result, check_keys=check_keys)
        return result

    def merge_configs(defaults: OptionValues,
                      vals: OptionValues) -> OptionValues:
        return {k: (merge_dicts(v, vals.get(k, {}))
                    if isinstance(v, dict)
                    else vals.get(k, v))
                for k, v in defaults.items()}

    Options, defaults = init_config(config_lines(all_options), parse_config)
    configs = list(resolve_config('/etc/xdg/kitty/grab.conf',
                                  os.path.join(config_dir, 'grab.conf'),
                                  config_files_on_cmd_line=None))
    return load_config(Options, defaults, parse_config, merge_configs, *configs)


def unstyled(s: str) -> str:
    return re.sub(r'\x1b\[[0-9;:]*m', '', s)


def string_slice(s: str, start_x: ScreenColumn,
                 end_x: ScreenColumn) -> Tuple[str, bool]:
    prev_pos = (truncate_point_for_length(s, start_x - 1) if start_x > 0
                else None)
    start_pos = truncate_point_for_length(s, start_x)
    end_pos = truncate_point_for_length(s, end_x - 1) + 1
    return s[start_pos:end_pos], prev_pos == start_pos


ActionName = str
ActionArgs = tuple
DirectionStr = str
RegionTypeStr = str


class GrabHandler(Handler):
    def __init__(self, args: Namespace, opts: Options,
                 lines: List[str]) -> None:
        super().__init__()
        self.args = args
        self.opts = opts
        self.lines = lines
        self.point = Position(args.x, args.y, args.top_line)
        self.mark = None           # type: Optional[Position]
        self.mark_type = NoRegion  # type: Type[Region]
        self.result = None         # type: Optional[ResultDict]
        for key_def, action in self.opts.key_definitions.items():
            self.add_shortcut(action, *key_def)

    def _start_end(self) -> Tuple[Position, Position]:
        start, end = sorted([self.point, self.mark or self.point])
        return self.mark_type.adjust(start, end)

    def _draw_line(self, current_line: AbsoluteLine) -> None:
        y = current_line - self.point.top_line  # type: ScreenLine
        line = self.lines[current_line - 1]
        clear_eol = '\x1b[m\x1b[K'
        sgr0 = '\x1b[m'

        plain = unstyled(line)
        selection_sgr = '\x1b[38{};48{}m'.format(
            color_as_sgr(self.opts.selection_foreground),
            color_as_sgr(self.opts.selection_background))
        start, end = self._start_end()

        # anti-flicker optimization
        if self.mark_type.line_inside_region(current_line, start, end):
            self.cmd.set_cursor_position(0, y)
            self.print('{}{}'.format(selection_sgr, plain),
                       end=clear_eol)
            return

        self.cmd.set_cursor_position(0, y)
        self.print('{}{}'.format(sgr0, line), end=clear_eol)

        if self.mark_type.line_outside_region(current_line, start, end):
            return

        start_x, end_x = self.mark_type.selection_in_line(
            current_line, start, end, wcswidth(plain))
        if start_x is None or end_x is None:
            return

        line_slice, half = string_slice(plain, start_x, end_x)
        self.cmd.set_cursor_position(start_x - (1 if half else 0), y)
        self.print('{}{}'.format(selection_sgr, line_slice), end='')

    def _update(self) -> None:
        self.cmd.set_window_title('Grab – {} {} {},{}+{} to {},{}+{}'.format(
            self.args.title,
            self.mark_type.name,
            getattr(self.mark, 'x', None), getattr(self.mark, 'y', None),
            getattr(self.mark, 'top_line', None),
            self.point.x, self.point.y, self.point.top_line))
        self.cmd.set_cursor_position(self.point.x, self.point.y)

    def _redraw_lines(self, lines: Iterable[AbsoluteLine]) -> None:
        for line in lines:
            self._draw_line(line)
        self._update()

    def _redraw(self) -> None:
        self._redraw_lines(range(
            self.point.top_line,
            self.point.top_line + self.screen_size.rows))

    def initialize(self) -> None:
        self.cmd.set_window_title('Grab – {}'.format(self.args.title))
        self._redraw()

    def on_text(self, text: str, in_bracketed_paste: bool = False) -> None:
        action = self.shortcut_action(text)
        if action is None:
            return
        self.perform_action(action)

    def on_key(self, key_event: KeyEvent) -> None:
        action = self.shortcut_action(key_event)
        if (key_event.type not in [kk.PRESS, kk.REPEAT]
                or action is None):
            return
        self.perform_action(action)

    def perform_action(self, action: Tuple[ActionName, ActionArgs]) -> None:
        func, args = action
        getattr(self, func)(*args)

    def quit(self, *args: Any) -> None:
        self.quit_loop(1)

    directions = {'left': (-1, 0),
                  'right': (1, 0),
                  'up': (0, -1),
                  'down': (0, 1)}  # type: Dict[DirectionStr, Tuple[int, int]]
    region_types = {'stream': StreamRegion,
                    'columnar': ColumnarRegion
                   }  # type: Dict[RegionTypeStr, Type[Region]]

    def _ensure_mark(self, mark_type: Type[Region] = StreamRegion) -> None:
        need_redraw = mark_type is not self.mark_type
        self.mark_type = mark_type
        self.mark = (self.mark or self.point) if mark_type.uses_mark else None
        if need_redraw:
            self._redraw()

    def _scroll(self, dtop: int) -> None:
        if not (0 < self.point.top_line + dtop
                <= 1 + len(self.lines) - self.screen_size.rows):
            return
        self.point = self.point.moved(dtop=dtop)
        self._redraw()

    def scroll(self, direction: DirectionStr) -> None:
        self._scroll(dtop=self.directions[direction][1])

    def _select(self, dx: int, dy: int, mark_type: Type[Region]) -> None:
        self._ensure_mark(mark_type)
        if not 0 <= self.point.x + dx < self.screen_size.cols:
            return
        if not 0 <= self.point.y + dy < self.screen_size.rows:
            self._scroll(dtop=dy)
        else:
            self.point = self.point.moved(dx, dy)
        self._redraw_lines(self.mark_type.lines_affected(
            *self._start_end(), self.point.line, dx, dy))

    def move(self, direction: DirectionStr) -> None:
        self._select(*self.directions[direction], NoRegion)

    def select(self, region_type: RegionTypeStr,
               direction: DirectionStr) -> None:
        self._select(*self.directions[direction],
                     self.region_types[region_type])

    def confirm(self, *args: Any) -> None:
        start, end = self._start_end()
        self.result = {'copy': '\n'.join(
            line_slice
            for line in range(start.line, end.line + 1)
            for plain in [unstyled(self.lines[line - 1])]
            for start_x, end_x in [self.mark_type.selection_in_line(
                line, start, end, len(plain))]
            if start_x is not None and end_x is not None
            for line_slice, _half in [string_slice(plain, start_x, end_x)])}
        self.quit_loop(0)


def main(args: List[str]) -> Optional['ResultDict']:
    def ospec():
        return '''
--cursor-x
dest=x
type=int
(Internal) Starting cursor column, 0-based.


--cursor-y
dest=y
type=int
(Internal) Starting cursor line, 0-based.


--top-line
dest=top_line
type=int
(Internal) Window scroll offset, 1-based.


--title
(Internal)'''

    args, _rest = parse_args(args[1:], ospec)
    tty = open(os.ctermid())
    lines = (sys.stdin.buffer.read().decode('utf-8')
             .split('\n')[:-1])  # last line ends with \n, too
    sys.stdin = tty
    opts = parse_opts()
    handler = GrabHandler(args, opts, lines)
    loop = Loop()
    loop.loop(handler)
    return handler.result


WindowId = int


def handle_result(args: List[str], result: 'ResultDict',
                  target_window_id: WindowId, boss: Boss) -> None:
    if 'copy' in result:
        set_clipboard_string(result['copy'])
