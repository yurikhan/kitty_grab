from base64 import b64encode
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


class LineRegion(MarkedRegion):
    name = 'line'

    @staticmethod
    def line_inside_region(current_line: AbsoluteLine,
                           start: Position, end: Position) -> bool:
        return start.line <= current_line <= end.line

    @staticmethod
    def selection_in_line(
            current_line: AbsoluteLine, start: Position, end: Position,
            maxx: ScreenColumn) -> SelectionInLine:
        if LineRegion.line_outside_region(current_line, start, end):
            return None, None
        return 0, maxx

    @staticmethod
    def lines_affected(mark: Optional[Position], old_point: Position,
                       point: Position) -> Set[AbsoluteLine]:
        assert mark is not None
        return _span(mark.line, old_point.line, point.line)


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
        self._pending_find = None  # type: Optional[Tuple[bool, bool]]
        self._pending_yank = False
        self._last_find = None     # type: Optional[Tuple[str, bool, bool]]
        self._pending_search = None  # type: Optional[Tuple[bool, str]]
        self._last_search = None   # type: Optional[Tuple[bool, str]]

        # Operating System Command (OSC); command number 52
        # c — clipboard
        # p — primary
        # s — secondary
        self.copy_to = {'primary': b'p', 'secondary': b's'}.get(args.copy_to, b'c')


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
        self.cmd.set_default_colors(cursor=self.opts.cursor)
        self._redraw()

    def perform_default_key_action(self, key_event: KeyEvent) -> bool:
        return False

    def on_key_event(self, key_event: KeyEvent, in_bracketed_paste: bool = False) -> None:
        if key_event.type not in [kk.PRESS, kk.REPEAT]:
            return
        if self._pending_find is not None:
            forward, till = self._pending_find
            self._pending_find = None
            if key_event.key != 'ESCAPE' and key_event.text:
                self._do_find(key_event.text, forward, till)
            return
        if self._pending_search is not None:
            forward, query = self._pending_search
            if key_event.key == 'ESCAPE':
                self._pending_search = None
                self._clear_search_prompt()
                self._update()
            elif key_event.key == 'ENTER':
                self._commit_search()
            elif key_event.key == 'BACKSPACE':
                self._pending_search = (forward, query[:-1]) if query else None
                if self._pending_search:
                    self._update_search_prompt()
                else:
                    self._clear_search_prompt()
                    self._update()
            elif key_event.text:
                self._pending_search = (forward, query + key_event.text)
                self._update_search_prompt()
            return
        action = self.shortcut_action(key_event)
        if action is None:
            return
        if self._pending_yank and action[0] != 'yank':
            self._pending_yank = False
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
                  'line': LineRegion,
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

    def _screen_bottom_y(self) -> ScreenLine:
        return min(self.screen_size.rows - 1,
                  len(self.lines) - self.point.top_line)

    def screen_top(self) -> Position:
        return Position(self.point.x, 0, self.point.top_line)

    def screen_middle(self) -> Position:
        return Position(self.point.x, self._screen_bottom_y() // 2,
                        self.point.top_line)

    def screen_bottom(self) -> Position:
        return Position(self.point.x, self._screen_bottom_y(),
                        self.point.top_line)

    def _position_for_line(self, line_no: AbsoluteLine,
                           col: int) -> Position:
        line = unstyled(self.lines[line_no - 1])
        x = wcswidth(line[:col])
        rows = self.screen_size.rows
        top_line = self.point.top_line
        if line_no < top_line:
            top_line = line_no
        elif line_no >= top_line + rows:
            top_line = line_no - rows + 1
        return Position(x, line_no - top_line, top_line)

    def matching_bracket(self) -> Position:
        pairs = {'(': (')', 1), '[': (']', 1), '{': ('}', 1),
                ')': ('(', -1), ']': ('[', -1), '}': ('{', -1)}
        line_no = self.point.line
        line = unstyled(self.lines[line_no - 1])
        col = truncate_point_for_length(line, self.point.x)
        while col < len(line) and line[col] not in pairs:
            col += 1
        if col >= len(line):
            return self.point
        char = line[col]
        target, direction = pairs[char]
        depth = 1
        while True:
            col += direction
            if not (0 <= col < len(line)):
                line_no += direction
                if line_no < 1 or line_no > len(self.lines):
                    return self.point
                line = unstyled(self.lines[line_no - 1])
                col = 0 if direction > 0 else len(line) - 1
                if not (0 <= col < len(line)):
                    continue
            c = line[col]
            if c == char:
                depth += 1
            elif c == target:
                depth -= 1
                if depth == 0:
                    return self._position_for_line(line_no, col)

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

    def _class_pred(self, c: str, big: bool) -> Callable[[str], bool]:
        if big:
            return ((lambda ch: not ch.isspace()) if not c.isspace()
                    else (lambda ch: ch.isspace()))
        return (self._is_word_char if self._is_word_char(c)
                else self._is_word_separator)

    def _word_left(self, big: bool = False) -> Position:
        if self.point.x > 0:
            line = unstyled(self.lines[self.point.line - 1])
            pos = truncate_point_for_length(line, self.point.x)
            pred = self._class_pred(line[pos - 1], big)
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

    def _word_right(self, big: bool = False) -> Position:
        line = unstyled(self.lines[self.point.line - 1])
        pos = truncate_point_for_length(line, self.point.x)
        if pos < len(line):
            pred = self._class_pred(line[pos], big)
            new_pos = pos + len(''.join(takewhile(pred, line[pos:])))
            return Position(wcswidth(line[:new_pos]),
                            self.point.y, self.point.top_line)
        if self.point.y < self.screen_size.rows - 1:
            return Position(0, self.point.y + 1, self.point.top_line)
        if self.point.top_line + self.point.y < len(self.lines):
            return Position(0, self.point.y, self.point.top_line + 1)
        return self.point

    def _word_end(self, big: bool = False) -> Position:
        line = unstyled(self.lines[self.point.line - 1])
        pos = truncate_point_for_length(line, self.point.x)
        n = len(line)
        i = pos + 1
        while i < n and line[i].isspace():
            i += 1
        if i >= n:
            if self.point.y < self.screen_size.rows - 1:
                return Position(0, self.point.y + 1, self.point.top_line)
            if self.point.top_line + self.point.y < len(self.lines):
                return Position(0, self.point.y, self.point.top_line + 1)
            return self.point
        pred = self._class_pred(line[i], big)
        while i + 1 < n and not line[i + 1].isspace() and pred(line[i + 1]):
            i += 1
        return Position(wcswidth(line[:i + 1]), self.point.y, self.point.top_line)

    def word_left(self) -> Position:
        return self._word_left()

    def word_right(self) -> Position:
        return self._word_right()

    def word_end(self) -> Position:
        return self._word_end()

    def big_word_left(self) -> Position:
        return self._word_left(big=True)

    def big_word_right(self) -> Position:
        return self._word_right(big=True)

    def big_word_end(self) -> Position:
        return self._word_end(big=True)

    def _find_char_position(self, char: str, forward: bool, till: bool,
                            extra_skip: int = 0) -> Position:
        line = unstyled(self.lines[self.point.line - 1])
        pos = truncate_point_for_length(line, self.point.x)
        if forward:
            idx = line.find(char, pos + 1 + extra_skip)
            if idx == -1:
                return self.point
            target = idx - 1 if till else idx
        else:
            search_end = pos - 1 - extra_skip
            if search_end < 0:
                return self.point
            idx = line.rfind(char, 0, search_end + 1)
            if idx == -1:
                return self.point
            target = idx + 1 if till else idx
        return Position(wcswidth(line[:target]), self.point.y, self.point.top_line)

    def find(self, direction: str, kind: str) -> None:
        self._pending_find = (direction == 'forward', kind == 'till')

    def _do_find(self, char: str, forward: bool, till: bool,
                extra_skip: int = 0) -> None:
        self._last_find = (char, forward, till)
        self._move_to(self._find_char_position(char, forward, till, extra_skip),
                      self.mode_types[self.mode])

    def repeat_find(self, direction: str) -> None:
        if self._last_find is None:
            return
        char, forward, till = self._last_find
        same = direction == 'same'
        extra_skip = 1 if (till and same) else 0
        self._do_find(char, forward if same else not forward, till, extra_skip)

    @staticmethod
    def _compile_search_query(query: str) -> 're.Pattern':
        try:
            return re.compile(query)
        except re.error:
            return re.compile(re.escape(query))

    @staticmethod
    def _rfind_regex(pattern: 're.Pattern', line: str, end: int) -> int:
        last = -1
        for m in pattern.finditer(line):
            if m.start() >= end:
                break
            last = m.start()
        return last

    def _search_position(self, forward: bool, query: str) -> Optional[Position]:
        pattern = self._compile_search_query(query)
        n = len(self.lines)
        cur_line_no = self.point.line
        cur_line = unstyled(self.lines[cur_line_no - 1])
        col = truncate_point_for_length(cur_line, self.point.x)
        if forward:
            m = pattern.search(cur_line, col + 1)
            if m is not None:
                return self._position_for_line(cur_line_no, m.start())
            for offset in range(1, n):
                line_no = (cur_line_no - 1 + offset) % n + 1
                line = unstyled(self.lines[line_no - 1])
                m = pattern.search(line)
                if m is not None:
                    return self._position_for_line(line_no, m.start())
            m = pattern.search(cur_line)
            idx = m.start() if m is not None else -1
            return (self._position_for_line(cur_line_no, idx)
                    if idx not in (-1, col) else None)
        else:
            idx = self._rfind_regex(pattern, cur_line, col) if col > 0 else -1
            if idx != -1:
                return self._position_for_line(cur_line_no, idx)
            for offset in range(1, n):
                line_no = (cur_line_no - 1 - offset) % n + 1
                line = unstyled(self.lines[line_no - 1])
                idx = self._rfind_regex(pattern, line, len(line) + 1)
                if idx != -1:
                    return self._position_for_line(line_no, idx)
            idx = self._rfind_regex(pattern, cur_line, len(cur_line) + 1)
            return (self._position_for_line(cur_line_no, idx)
                    if idx not in (-1, col) else None)

    def start_search(self, direction: str) -> None:
        self._pending_search = (direction == 'forward', '')
        self._update_search_prompt()

    def _search_prompt_row(self) -> ScreenLine:
        return self.screen_size.rows - 1

    def _update_search_prompt(self) -> None:
        if self._pending_search is None:
            return
        forward, query = self._pending_search
        prefix = '/' if forward else '?'
        text = '{}{}'.format(prefix, query)
        y = self._search_prompt_row()
        self.cmd.set_cursor_position(0, y)
        self.print('\x1b[m{}'.format(text), end='\x1b[m\x1b[K')
        self.cmd.set_cursor_position(wcswidth(text), y)
        self.cmd.set_window_title('Grab – {}'.format(text))

    def _clear_search_prompt(self) -> None:
        abs_line = self.point.top_line + self._search_prompt_row()
        if abs_line <= len(self.lines):
            self._draw_line(abs_line)
        else:
            self.cmd.set_cursor_position(0, self._search_prompt_row())
            self.print('\x1b[m', end='\x1b[K')

    def _do_search(self, forward: bool, query: str) -> None:
        pos = self._search_position(forward, query)
        if pos is not None:
            self._move_to(pos, self.mode_types[self.mode])
        else:
            self._update()

    def _commit_search(self) -> None:
        forward, query = self._pending_search
        self._pending_search = None
        self._clear_search_prompt()
        if query:
            self._last_search = (forward, query)
            self._do_search(forward, query)
        else:
            self._update()

    def repeat_search(self, direction: str) -> None:
        if self._last_search is None:
            return
        forward, query = self._last_search
        self._do_search(forward if direction == 'same' else not forward, query)

    def yank(self) -> None:
        if self.mark is not None:
            self.confirm()
            return
        if self._pending_yank:
            self._pending_yank = False
            self.yank_line()
            return
        self._pending_yank = True

    def yank_line(self) -> None:
        self.result = {'copy': unstyled(self.lines[self.point.line - 1])}
        self.quit_loop(0)

    def yank_to_end(self) -> None:
        line = unstyled(self.lines[self.point.line - 1])
        col = truncate_point_for_length(line, self.point.x)
        self.result = {'copy': line[col:]}
        self.quit_loop(0)

    def _move_to(self, new_point: Position, mark_type: Type[Region]) -> None:
        self._ensure_mark(mark_type)
        old_point = self.point
        self.point = new_point
        if self.point.top_line != old_point.top_line:
            self._redraw()
        else:
            self._redraw_lines(self.mark_type.lines_affected(
                self.mark, old_point, self.point))

    def _select(self, direction: DirectionStr,
                mark_type: Type[Region]) -> None:
        self._move_to((getattr(self, direction))(), mark_type)

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
--copy-to
dest=copy_to
type=str
Copy to: 'clipboard' or 'primary'/selection or 'secondary' buffer


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
        if loop.return_code == 0 and 'copy' in handler.result:
            sys.stdout.buffer.write(b''.join((b'\x1b]52;', handler.copy_to, b';',
                                              b64encode(handler.result['copy'].encode('utf-8')),
                                              b'\x1b\\')))
        return {}
    except Exception as e:
        from kittens.tui.loop import debug
        from traceback import format_exc
        debug(format_exc())
        raise
