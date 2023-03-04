from functools import total_ordering
from itertools import takewhile
import json
import os.path
import re
import sys
from typing import (TYPE_CHECKING, Any, Callable, Dict, Iterable, List,
                    NamedTuple, Optional, Set, Tuple, Type, Union)
import unicodedata

from kitty.boss import Boss
from kitty.cli import parse_args
from kitten_options_types import Options, defaults
from kitten_options_parse import create_result_dict, merge_result_dicts, parse_conf_item
from kitty.conf.utils import load_config as _load_config, parse_config_base, resolve_config
from kitty.constants import config_dir
from kitty.fast_data_types import truncate_point_for_length, wcswidth
import kitty.key_encoding as kk
from kitty.key_encoding import KeyEvent
from kitty.rgb import color_as_sgr
from kittens.tui.handler import Handler
from kittens.tui.loop import Loop


try:
    from kitty.clipboard import set_clipboard_string
except ImportError:
    from kitty.fast_data_types import set_clipboard_string


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
OptionName = str
OptionValues = Dict[OptionName, Any]
TypeMap = Dict[OptionName, Callable[[Any], Any]]


def load_config(*paths: str, overrides: Optional[Iterable[str]] = None) -> Options:

    def parse_config(lines: Iterable[str]) -> Dict[str, Any]:
        ans: Dict[str, Any] = create_result_dict()
        parse_config_base(
            lines,
            parse_conf_item,
            ans,
        )
        return ans

    configs = list(resolve_config('/etc/xdg/kitty/grab.conf',
                                  os.path.join(config_dir, 'grab.conf'),
                                  config_files_on_cmd_line=[]))
    overrides = tuple(overrides) if overrides is not None else ()
    opts_dict, paths = _load_config(defaults, parse_config, merge_result_dicts, *configs, overrides=overrides)
    opts = Options(opts_dict)
    opts.config_paths = paths
    opts.config_overrides = overrides
    return opts


def unstyled(s: str) -> str:
    s = re.sub(r'\x1b\[[0-9;:]*m', '', s)
    s = re.sub(r'\x1b\](?:[^\x07\x1b]+|\x1b[^\\])*(?:\x1b\\|\x07)', '', s)
    return s


def string_slice(s: str, start_x: ScreenColumn,
                 end_x: ScreenColumn) -> Tuple[str, bool]:
    prev_pos = (truncate_point_for_length(s, start_x - 1) if start_x > 0
                else None)
    start_pos = truncate_point_for_length(s, start_x)
    end_pos = truncate_point_for_length(s, end_x - 1) + 1
    return s[start_pos:end_pos], prev_pos == start_pos


DirectionStr = str
RegionTypeStr = str
ModeTypeStr = str


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
        self.mode = 'normal'       # type: ModeTypeStr
        self.result = None         # type: Optional[ResultDict]
        for spec, action in self.opts.map:
            self.add_shortcut(action, spec)

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

    def perform_default_key_action(self, key_event: KeyEvent) -> bool:
        return False

    def on_key_event(self, key_event: KeyEvent, in_bracketed_paste: bool = False) -> None:
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

    mode_types = {'normal': NoRegion,
                  'visual': StreamRegion,
                  'block': ColumnarRegion,
                  }  # type: Dict[ModeTypeStr, Type[Region]]

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

    def noop(self) -> Position:
        return self.point

    @property
    def _select_by_word_characters(self) -> str:
        return (self.opts.select_by_word_characters
                or (json.loads(os.getenv('KITTY_COMMON_OPTS', '{}'))
                    .get('select_by_word_characters', '@-./_~?&=%+#')))

    def _is_word_char(self, c: str) -> bool:
        return (unicodedata.category(c)[0] in 'LN'
                or c in self._select_by_word_characters)

    def _is_word_separator(self, c: str) -> bool:
        return (unicodedata.category(c)[0] not in 'LN'
                and c not in self._select_by_word_characters)

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
        self._select(direction, self.mode_types[self.mode])

    def select(self, region_type: RegionTypeStr,
               direction: DirectionStr) -> None:
        self._select(direction, self.region_types[region_type])

    def set_mode(self, mode: ModeTypeStr) -> None:
        self.mode = mode
        self._select('noop', self.mode_types[mode])

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
    def ospec() -> str:
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
        opts = load_config()
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
