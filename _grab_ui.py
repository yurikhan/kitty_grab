from functools import total_ordering
from itertools import takewhile
import json
import os.path
import re
import sys
from typing import (TYPE_CHECKING, Any, Callable, Dict, Iterable, List,
                    NamedTuple, Optional, Set, Tuple, Type, Union)
import unicodedata

from kitty.boss import Boss                       # type: ignore
from kitty.cli import parse_args                  # type: ignore
from kitty.conf.definition import (               # type: ignore
    Option, config_lines, option_func)
from kitty.conf.utils import (                    # type: ignore
    init_config, key_func, load_config, merge_dicts, parse_config_base,
    parse_kittens_key as _parse_kittens_key, resolve_config, to_color)
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

    def scrolled(self, dtop: int = 0) -> 'Position':
        """
        Return a new position equivalent to self
        but scrolled dtop lines.
        """
        return self.moved(dy=-dtop, dtop=dtop)

    def scrolled_up(self, rows: ScreenLine) -> 'Position':
        """
        Return a new position equivalent to self
        but with top_line as small as possible.
        """
        return self.scrolled(-min(self.top_line - 1,
                                  rows - 1 - self.y))

    def scrolled_down(self, rows: ScreenLine,
                      lines: AbsoluteLine) -> 'Position':
        """
        Return a new position equivalent to self
        but with top_line as large as possible.
        """
        return self.scrolled(min(lines - rows + 1 - self.top_line,
                                 self.y))

    def scrolled_towards(self, other: 'Position', rows: ScreenLine,
                         lines: Optional[AbsoluteLine] = None) -> 'Position':
        """
        Return a new position equivalent to self.
        If self and other fit within a single screen,
        scroll as little as possible to make both visible.
        Otherwise, scroll as much as possible towards other.
        """
        #  @ 
        #  .|   .    @|   .    .
        # |.|  |.   |.|  |.   |.|
        # |*|  |*|  |*|  |*|  |*|
        # |.   |.|  |.   |.|  |@|
        #  .    .|   .    @|   .
        #       @
        if other.line <= self.line - rows:         # above, unreachable
            return self.scrolled_up(rows)
        if other.line >= self.line + rows:         # below, unreachable
            assert lines is not None
            return self.scrolled_down(rows, lines)
        if other.line < self.top_line:             # above, reachable
            return self.scrolled(other.line - self.top_line)
        if other.line > self.top_line + rows - 1:  # below, reachable
            return self.scrolled(other.line - self.top_line - rows + 1)
        return self                                # visible

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


def _span(line: AbsoluteLine, *lines: AbsoluteLine) -> Set[AbsoluteLine]:
    return set(range(min(line, *lines), max(line, *lines) + 1))


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
    def lines_affected(mark: Optional[Position], old_point: Position,
                       point: Position) -> Set[AbsoluteLine]:
        """
        Return the set of lines (1-based, top of scrollback, down)
        that must be redrawn when point moves from old_point.
        """
        return set()

    @staticmethod
    def page_up(mark: Optional[Position], point: Position,
                rows: ScreenLine, lines: AbsoluteLine) -> Position:
        """
        Return the position page up from point.
        """
        #                          ........
        #                          ....$...|
        #  ........    ....$...|   ........|
        # |....$...|  |....^...|  |....^...|
        # |....^...|  |........|  |........
        # |........|  |........   |........
        #  ........    ........    ........
        if point.y > 0:
            return Position(point.x, 0, point.top_line)
        assert point.y == 0
        return Position(point.x, 0,
                        max(1, point.top_line - rows + 1))

    @staticmethod
    def page_down(mark: Optional[Position], point: Position,
                  rows: ScreenLine, lines: AbsoluteLine) -> Position:
        """
        Return the position page down from point.
        """
        #  ........    ........    ........
        # |........|  |........   |........
        # |....^...|  |........|  |........
        # |....$...|  |....^...|  |....^...|
        #  ........    ....$...|   ........|
        #                          ....$...|
        #                          ........
        maxy = rows - 1
        if point.y < maxy:
            return Position(point.x, maxy, point.top_line)
        assert point.y == maxy
        return Position(point.x, maxy,
                        min(lines - maxy, point.top_line + maxy))


class NoRegion(Region):
    name = 'unselected'
    uses_mark = False

    @staticmethod
    def line_outside_region(current_line: AbsoluteLine,
                            start: Position, end: Position) -> bool:
        return False


class MarkedRegion(Region):
    uses_mark = True

    # When a region is marked,
    # override page up and down motion
    # to keep as much region visible as possible.
    #
    # This means,
    # after computing the position in the usual way,
    # do the minimum possible scroll adjustment
    # to bring both mark and point on screen.
    # If that is not possible,
    # do the maximum possible scroll adjustment
    # towards mark
    # that keeps point on screen.
    @staticmethod
    def page_up(mark: Optional[Position], point: Position,
                rows: ScreenLine, lines: AbsoluteLine) -> Position:
        assert mark is not None
        return (Region.page_up(mark, point, rows, lines)
                .scrolled_towards(mark, rows, lines))

    @staticmethod
    def page_down(mark: Optional[Position], point: Position,
                  rows: ScreenLine, lines: AbsoluteLine) -> Position:
        assert mark is not None
        return (Region.page_down(mark, point, rows, lines)
                .scrolled_towards(mark, rows, lines))


class StreamRegion(MarkedRegion):
    name = 'stream'

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
    def lines_affected(mark: Optional[Position], old_point: Position,
                       point: Position) -> Set[AbsoluteLine]:
        return _span(old_point.line, point.line)


class ColumnarRegion(MarkedRegion):
    name = 'columnar'

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
    def lines_affected(mark: Optional[Position], old_point: Position,
                       point: Position) -> Set[AbsoluteLine]:
        assert mark is not None
        # If column changes, all lines change.
        if old_point.x != point.x:
            return _span(mark.line, old_point.line, point.line)
        # If point passes mark, all passed lines change except mark line.
        if old_point < mark < point or point < mark < old_point:
            return _span(old_point.line, point.line) - {mark.line}
        # If point moves away from mark,
        # all passed lines change except old point line.
        elif mark < old_point < point or point < old_point < mark:
            return _span(old_point.line, point.line) - {old_point.line}
        # Otherwise, point moves toward mark,
        # and all passed lines change except new point line.
        else:
            return _span(old_point.line, point.line) - {point.line}


ActionName = str
ActionArgs = tuple
ShortcutMods = int
KeyName = str
Namespace = Any  # kitty.cli.Namespace (< 0.17.0)
Options = Any  # dynamically created namespace class
OptionName = str
OptionValues = Dict[OptionName, Any]
OptionDefs = Dict[OptionName, Option]
TypeMap = Dict[OptionName, Callable[[Any], Any]]


class TypeConvert:  # compatibility shim for 0.17.0
    """
    The kitty.conf.utils.parse_config_base function
    has an argument that specifies the rules
    for converting a configuration option value
    from string(?) read from the config file
    to its application-specific type.

    Before 0.17.0, this argument has type TypeMap.
    parse_config_base takes the element by OptionName key
    calls it on the raw value
    and expects it to return the converted value.

    Starting with 0.17.0, it has type Callable[[OptionName, Any], Any]
    and is called directly with the OptionName and raw value,
    and expected to return the converted value.

    This class implements both interfaces as a temporary measure.
    """
    def __init__(self, type_map: TypeMap) -> None:
        self._type_map = type_map

    def __getitem__(self, key: OptionName) -> Callable[[Any], Any]:
        return self._type_map[key]

    def get(self, key: OptionName,
            default: Callable[[Any], Any] = None) -> Callable[[Any], Any]:
        return self._type_map.get(key, default)

    def __call__(self, key: OptionName, value: Any) -> Any:
        return self._type_map.get(key, lambda v: v)(value)


def parse_opts() -> Options:
    all_options = {}  # type: OptionDefs
    o, k, g, _all_groups = option_func(all_options, {
        'shortcuts': ['Keyboard shortcuts'],
        'colors': ['Colors'],
        'behavior': ['Behavior']
    })

    g('shortcuts')
    k('quit', 'q', 'quit')
    k('quit', 'esc', 'quit')
    k('confirm', 'enter', 'confirm')
    k('left', 'left', 'move left')
    k('right', 'right', 'move right')
    k('up', 'up', 'move up')
    k('down', 'down', 'move down')
    k('page up', 'page_up', 'move page up')
    k('page down', 'page_down', 'move page down')
    k('start of line', 'home', 'move first')
    k('first non-whitespace', 'a', 'move first nonwhite')
    k('last non-whitespace', 'end', 'move last nonwhite')
    k('end of line', 'e', 'move last')
    k('start of buffer', 'ctrl+home', 'move top')
    k('end of buffer', 'ctrl+end', 'move bottom')
    k('word left', 'ctrl+left', 'move word left')
    k('word right', 'ctrl+right', 'move word right')
    k('scroll up', 'ctrl+up', 'scroll up')
    k('scroll down', 'ctrl+down', 'scroll down')
    k('select left', 'shift+left', 'select stream left')
    k('select right', 'shift+right', 'select stream right')
    k('select up', 'shift+up', 'select stream up')
    k('select down', 'shift+down', 'select stream down')
    k('select page up', 'shift+page_up', 'select stream page up')
    k('select page down', 'shift+page_down', 'select stream page down')
    k('select to start of line', 'shift+home', 'select stream first')
    k('select to first non-whitespace', 'A', 'select stream first nonwhite')
    k('select to last non-whitespace', 'shift+end', 'select stream last nonwhite')
    k('select to end of line', 'E', 'select stream last')
    k('select to start of buffer', 'shift+ctrl+home', 'select stream top')
    k('select to end of buffer', 'shift+ctrl+end', 'select stream bottom')
    k('select word left', 'shift+ctrl+left', 'select stream word left')
    k('select word right', 'shift+ctrl+right', 'select stream word right')
    k('column select left', 'alt+left', 'select columnar left')
    k('column select right', 'alt+right', 'select columnar right')
    k('column select up', 'alt+up', 'select columnar up')
    k('column select down', 'alt+down', 'select columnar down')
    k('column select page up', 'alt+page_up', 'select columnar page up')
    k('column select page down', 'alt+page_down', 'select columnar page down')
    k('column select to start of line', 'alt+home', 'select columnar first')
    k('column select to first non-whitespace', 'alt+A', 'select columnar first nonwhite')
    k('column select to last non-whitespace', 'alt+end', 'select columnar last nonwhite')
    k('column select to end of line', 'alt+E', 'select columnar last')
    k('column select to start of buffer', 'alt+ctrl+home', 'select columnar top')
    k('column select to end of buffer', 'alt+ctrl+end', 'select columnar bottom')
    k('column select word left', 'alt+ctrl+left', 'select columnar word left')
    k('column select word right', 'alt+ctrl+right', 'select columnar word right')

    g('colors')
    o('selection_foreground', '#FFFFFF', option_type=to_color)
    o('selection_background', '#5294E2', option_type=to_color)

    g('behavior')
    o('select_by_word_characters',
      json.loads(os.getenv('KITTY_COMMON_OPTS'))['select_by_word_characters'],
      option_type=str)

    type_map = TypeConvert({o.name: o.option_type
                            for o in all_options.values()
                            if hasattr(o, 'option_type')})

    defaults = None

    # Parsers/validators for key binding directives
    func_with_args, args_funcs = key_func()

    def parse_region_type(region_type: str) -> str:
        result = region_type.lower()
        assert result in ['stream', 'columnar']
        return result

    def parse_direction(direction: str) -> str:
        direction_lc = direction.lower()
        assert direction_lc in ['left', 'right', 'up', 'down',
                                'page up', 'page down',
                                'first', 'first nonwhite',
                                'last nonwhite', 'last',
                                'top', 'bottom',
                                'word left', 'word right']
        return direction_lc.replace(' ', '_')

    def parse_scroll_direction(direction: str) -> str:
        result = direction.lower()
        assert result in ['up', 'down']
        return result

    @func_with_args('move')
    def move(func: Callable, direction: str) -> Tuple[Callable, str]:
        return func, parse_direction(direction)

    @func_with_args('scroll')
    def scroll(func: Callable, direction: str) -> Tuple[Callable, str]:
        return func, parse_scroll_direction(direction)

    @func_with_args('select')
    def select(func: Callable, args: str) -> Tuple[Callable, Tuple[str, str]]:
        region_type, direction = args.split(' ', 1)
        return func, (parse_region_type(region_type),
                      parse_direction(direction))

    def parse_kittens_key(val: str, args_funcs: Dict[str, Callable]) -> Optional[Tuple[
            Tuple[ActionName, ActionArgs], KeyName, ShortcutMods, bool]]:
        parsed_key = _parse_kittens_key(val, args_funcs)
        if parsed_key is None:
            return None
        if len(parsed_key) == 2:  # kitty ≥ 0.17.0
            action, (key, mods, is_text) = parsed_key
        else:                     # kitty < 0.17.0
            action, key, mods, is_text = parsed_key
        return (action, key, mods,
                is_text and (0 == (mods or 0) & (kk.CTRL | kk.ALT | kk.SUPER)))

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
        rows = self.screen_size.rows
        new_point = self.point.moved(dtop=dtop)
        if not (0 < new_point.top_line <= 1 + len(self.lines) - rows):
            return
        self.point = new_point
        self._redraw()

    def scroll(self, direction: DirectionStr) -> None:
        self._scroll(dtop={'up': -1, 'down': 1}[direction])

    def left(self) -> Position:
        return self.point.moved(dx=-1) if self.point.x > 0 else self.point

    def right(self) -> Position:
        return (self.point.moved(dx=1)
                if self.point.x + 1 < self.screen_size.cols
                else self.point)

    def up(self) -> Position:
        return (self.point.moved(dy=-1) if self.point.y > 0 else
                self.point.moved(dtop=-1) if self.point.top_line > 0 else
                self.point)

    def down(self) -> Position:
        return (self.point.moved(dy=1)
                if self.point.y + 1 < self.screen_size.rows
                else self.point.moved(dtop=1)
                if self.point.line < len(self.lines)
                else self.point)

    def page_up(self) -> Position:
        return self.mark_type.page_up(
            self.mark, self.point, self.screen_size.rows,
            max(self.screen_size.rows, len(self.lines)))

    def page_down(self) -> Position:
        return self.mark_type.page_down(
            self.mark, self.point, self.screen_size.rows,
            max(self.screen_size.rows, len(self.lines)))

    def first(self) -> Position:
        return Position(0, self.point.y, self.point.top_line)

    def first_nonwhite(self) -> Position:
        line = unstyled(self.lines[self.point.line - 1])
        prefix = ''.join(takewhile(str.isspace, line))
        return Position(wcswidth(prefix), self.point.y, self.point.top_line)

    def last_nonwhite(self) -> Position:
        line = unstyled(self.lines[self.point.line - 1])
        suffix = ''.join(takewhile(str.isspace, reversed(line)))
        return Position(wcswidth(line[:len(line) - len(suffix)]),
                        self.point.y, self.point.top_line)

    def last(self) -> Position:
        return Position(self.screen_size.cols,
                        self.point.y, self.point.top_line)

    def top(self) -> Position:
        return Position(0, 0, 1)

    def bottom(self) -> Position:
        x = wcswidth(unstyled(self.lines[-1]))
        y = min(len(self.lines) - self.point.top_line,
                self.screen_size.rows - 1)
        return Position(x, y, len(self.lines) - y)

    def _is_word_char(self, c: str) -> bool:
        return (unicodedata.category(c)[0] in 'LN'
                or c in self.opts.select_by_word_characters)

    def _is_word_separator(self, c: str) -> bool:
        return (unicodedata.category(c)[0] not in 'LN'
                and c not in self.opts.select_by_word_characters)

    def word_left(self) -> Position:
        if self.point.x > 0:
            line = unstyled(self.lines[self.point.line - 1])
            pos = truncate_point_for_length(line, self.point.x)
            pred = (self._is_word_char if self._is_word_char(line[pos - 1])
                    else self._is_word_separator)
            new_pos = pos - len(''.join(takewhile(pred, reversed(line[:pos]))))
            return Position(wcswidth(line[:new_pos]),
                            self.point.y, self.point.top_line)
        if self.point.y > 0:
            return Position(wcswidth(unstyled(self.lines[self.point.line - 2])),
                            self.point.y - 1, self.point.top_line)
        if self.point.top_line > 1:
            return Position(wcswidth(unstyled(self.lines[self.point.line - 2])),
                            self.point.y, self.point.top_line - 1)
        return self.point

    def word_right(self) -> Position:
        line = unstyled(self.lines[self.point.line - 1])
        pos = truncate_point_for_length(line, self.point.x)
        if pos < len(line):
            pred = (self._is_word_char if self._is_word_char(line[pos])
                    else self._is_word_separator)
            new_pos = pos + len(''.join(takewhile(pred, line[pos:])))
            return Position(wcswidth(line[:new_pos]),
                            self.point.y, self.point.top_line)
        if self.point.y < self.screen_size.rows - 1:
            return Position(0, self.point.y + 1, self.point.top_line)
        if self.point.top_line + self.point.y < len(self.lines):
            return Position(0, self.point.y, self.point.top_line + 1)
        return self.point

    def _select(self, direction: DirectionStr,
                mark_type: Type[Region]) -> None:
        self._ensure_mark(mark_type)
        old_point = self.point
        self.point = (getattr(self, direction))()
        if self.point.top_line != old_point.top_line:
            self._redraw()
        else:
            self._redraw_lines(self.mark_type.lines_affected(
                self.mark, old_point, self.point))

    def move(self, direction: DirectionStr) -> None:
        self._select(direction, NoRegion)

    def select(self, region_type: RegionTypeStr,
               direction: DirectionStr) -> None:
        self._select(direction, self.region_types[region_type])

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

    try:
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
    except Exception as e:
        from kittens.tui.loop import debug
        from traceback import format_exc
        debug(format_exc())
        raise


WindowId = int


def handle_result(args: List[str], result: 'ResultDict',
                  target_window_id: WindowId, boss: Boss) -> None:
    if 'copy' in result:
        set_clipboard_string(result['copy'])
