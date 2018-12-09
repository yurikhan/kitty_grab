from collections import namedtuple
from functools import total_ordering
import os.path
import re
import sys

from kitty.cli import parse_args
from kitty.conf.definition import config_lines, option_func
from kitty.conf.utils import (
    init_config, key_func, load_config, merge_dicts, parse_config_base,
    parse_kittens_key, resolve_config, to_color)
from kitty.constants import config_dir
from kitty.fast_data_types import set_clipboard_string
import kitty.key_encoding as kk
from kitty.rgb import color_as_sgr
from kittens.tui.handler import Handler
from kittens.tui.loop import Loop


STREAM, COLUMNAR = range(2)


PositionBase = namedtuple('Position', ['x', 'y', 'top_line'])
@total_ordering
class Position(PositionBase):
    @property
    def line(self):
        return self.y + self.top_line

    def moved(self, dx=0, dy=0, dtop=0):
        return self._replace(x=self.x + dx, y=self.y + dy,
                             top_line=self.top_line + dtop)

    def __lt__(self, other):
        return (self.line, self.x) < (other.line, other.x)


def parse_opts():
    all_options = {}
    o, k, g, all_groups = option_func(all_options, {
        'shortcuts': ['Keyboard shortcuts'],
        'colors': ['Colors']
    })

    g('shortcuts')
    k('quit', 'q', 'quit')
    k('quit', 'esc', 'quit')
    k('left', 'left', 'move_left')
    k('right', 'right', 'move_right')
    k('up', 'up', 'move_up')
    k('down', 'down', 'move_down')
    k('scroll up', 'ctrl+up', 'scroll_up')
    k('scroll down', 'ctrl+down', 'scroll_down')
    k('select left', 'shift+left', 'select_left')
    k('select right', 'shift+right', 'select_right')
    k('select up', 'shift+up', 'select_up')
    k('select down', 'shift+down', 'select_down')
    k('column select left', 'alt+left', 'select_col_left')
    k('column select right', 'alt+right', 'select_col_right')
    k('column select up', 'alt+up', 'select_col_up')
    k('column select down', 'alt+down', 'select_col_down')
    k('confirm', 'enter', 'confirm')

    g('colors')
    o('selection_foreground', '#FFFFFF', option_type=to_color)
    o('selection_background', '#5294E2', option_type=to_color)

    type_map = {o.name: o.option_type
                for o in all_options.values()
                if hasattr(o, 'option_type')}

    defaults = None

    func_with_args, args_funcs = key_func()
    def special_handling(key, val, result):
        if key == 'map':
            action, *key_def = parse_kittens_key(val, args_funcs)
            result['key_definitions'][tuple(key_def)] = action
            return True

    def parse_config(lines, check_keys=True):
        result = {'key_definitions': {}}
        parse_config_base(lines, defaults, type_map, special_handling,
            result, check_keys=check_keys)
        return result

    Options, defaults = init_config(config_lines(all_options), parse_config)
    configs = list(resolve_config('/etc/xdg/kitty/grab.conf',
                                  os.path.join(config_dir, 'grab.conf'),
                                  config_files_on_cmd_line=None))
    return load_config(Options, defaults, parse_config, merge_dicts, *configs)


def unstyled(s):
    return re.sub(r'\x1b\[[0-9;:]*m', '', s)


class GrabHandler(Handler):
    def __init__(self, args, opts, lines):
        super().__init__()
        self.args = args
        self.opts = opts
        self.lines = lines
        self.point = Position(args.x, args.y, args.top_line)
        self.mark = None
        self.result = None
        for key_def, action in self.opts.key_definitions.items():
            self.add_shortcut(action, *key_def)

    def _visible_lines(self, point, mark):
        start, end = sorted([point, mark])
        yield from range(
            max(0, start.line - point.top_line),
            min(self.screen_size.rows, end.line + 1 - point.top_line))

    def _start_end(self):
        start, end = sorted([self.point, self.mark])
        if self.mark_type == COLUMNAR:
            start, end = (start._replace(x=min(start.x, end.x)),
                          end._replace(x=max(start.x, end.x)))
        return start, end

    def _draw_line(self, y):
        current_line = y + self.point.top_line
        line = self.lines[current_line - 1]
        clear_eol = '\x1b[m\x1b[K'
        sgr0 = '\x1b[m'

        if not self.mark:
            self.cmd.set_cursor_position(0, y)
            self.print('{}{}'.format(sgr0, line), end=clear_eol)
            return

        plain = unstyled(line)
        selection_sgr = '\x1b[38{};48{}m'.format(
            color_as_sgr(self.opts.selection_foreground),
            color_as_sgr(self.opts.selection_background))
        start, end = self._start_end()

        if (start.line < current_line < end.line
                and self.mark_type == STREAM):
            # line fully in region
            self.cmd.set_cursor_position(0, y)
            self.print('{}{}'.format(selection_sgr, plain),
                       end=clear_eol)
            return

        self.cmd.set_cursor_position(0, y)
        self.print('{}{}'.format(sgr0, line), end=clear_eol)

        if current_line < start.line or end.line < current_line:
            return

        start_x = start.x if (current_line == start.line
                              or self.mark_type == COLUMNAR) else 0
        # XXX: len(plain) and plain[start_x:end_x]
        # should be replaced with width-aware functions
        end_x = end.x if (current_line == end.line
                          or self.mark_type == COLUMNAR) else len(plain)

        self.cmd.set_cursor_position(start_x, y)
        self.print('{}{}'.format(selection_sgr, plain[start_x:end_x]),
                   end='')

    def _update(self):
        self.cmd.set_cursor_position(self.point.x, self.point.y)

    def _redraw_lines(self, *lines):
        for y in lines:
            self._draw_line(y)
        self._update()

    def _redraw(self):
        self._redraw_lines(*range(self.screen_size.rows))

    def initialize(self):
        self.cmd.set_window_title('Grab – {}'.format(self.args.title))
        self._redraw()

    def on_text(self, text, in_bracketed_paste=False):
        action = self.shortcut_action(text)
        if action is None:
            return
        self.perform_action(action)

    def on_key(self, key_event):
        action = self.shortcut_action(key_event)
        if (key_event.type not in [kk.PRESS, kk.REPEAT]
                or action is None):
            return
        self.perform_action(action)

    def perform_action(self, action):
        func, args = action
        getattr(self, func)(*args)

    def quit(self, *args):
        self.quit_loop(1)

    def _unset_mark(self):
        if self.mark:
            self.mark = None
            self._redraw()

    def _ensure_mark(self, mark_type=STREAM):
        self.mark = self.mark or self.point
        self.mark_type = mark_type

    def move_left(self, *args):
        self._unset_mark()
        if self.point.x == 0:
            return
        self.point = self.point.moved(-1)
        self._update()

    def move_right(self, *args):
        self._unset_mark()
        if self.point.x + 1 >= self.screen_size.cols:
            return
        self.point = self.point.moved(1)
        self._update()

    def move_up(self, *args):
        self._unset_mark()
        if self.point.y <= 0:
            return self.scroll_up()
        self.point = self.point.moved(0, -1)
        self._update()

    def move_down(self, *args):
        self._unset_mark()
        if self.point.y + 1 >= self.screen_size.rows:
            return self.scroll_down()
        self.point = self.point.moved(0, 1)
        self._update()

    def scroll_up(self, *args):
        if self.point.top_line <= 1:
            return
        self.point = self.point.moved(dtop=-1)
        self._redraw()

    def scroll_down(self, *args):
        if self.point.top_line + self.screen_size.rows >= 1 + len(self.lines):
            return
        self.point = self.point.moved(dtop=1)
        self._redraw()

    def select_left(self, *args):
        self._ensure_mark()
        if self.point.x == 0:
            return
        self.point = self.point.moved(-1)
        self._redraw_lines(self.point.y)

    def select_right(self, *args):
        self._ensure_mark()
        if self.point.x + 1 >= self.screen_size.cols: return
        self.point = self.point.moved(1)
        self._redraw_lines(self.point.y)

    def select_up(self, *args):
        self._ensure_mark()
        if self.point.y <= 0:
            self.scroll_up()
        else:
            self.point = self.point.moved(0, -1)
        self._redraw_lines(self.point.y, self.point.y + 1)

    def select_down(self, *args):
        self._ensure_mark()
        if self.point.y + 1 >= self.screen_size.rows:
            self.scroll_down()
        else:
            self.point = self.point.moved(0, 1)
        self._redraw_lines(self.point.y, self.point.y - 1)

    def select_col_left(self, *args):
        self._ensure_mark(COLUMNAR)
        if self.point.x == 0:
            return
        self.point = self.point.moved(-1)
        self._redraw_lines(*self._visible_lines(self.point, self.mark))

    def select_col_right(self, *args):
        self._ensure_mark(COLUMNAR)
        if self.point.x + 1 >= self.screen_size.cols: return
        self.point = self.point.moved(1)
        self._redraw_lines(*self._visible_lines(self.point, self.mark))

    def select_col_up(self, *args):
        self._ensure_mark(COLUMNAR)
        if self.point.y <= 0:
            self.scroll_up()
        else:
            self.point = self.point.moved(0, -1)
        self._redraw_lines(self.point.y, self.point.y + 1)

    def select_col_down(self, *args):
        self._ensure_mark(COLUMNAR)
        if self.point.y + 1 >= self.screen_size.rows:
            self.scroll_down()
        else:
            self.point = self.point.moved(0, 1)
        self._redraw_lines(self.point.y, self.point.y - 1)

    def confirm(self, *args):
        if not self.mark:
            return
        start, end = self._start_end()

        if self.mark_type == COLUMNAR:
            self.result = {'copy': '\n'.join([
                unstyled(l)[start.x:end.x]
                for l in self.lines[start.line - 1 : end.line]])}
            self.quit_loop(0)
            return

        if start.line == end.line:
            self.result = {'copy': unstyled(self.lines[start.line - 1])[start.x:end.x]}
            self.quit_loop(0)

        self.result = {'copy': '\n'.join(
            [unstyled(self.lines[start.line - 1])[start.x:]] +
            [unstyled(l) for l in self.lines[start.line : end.line - 2]] +
            [unstyled(self.lines[end.line - 1])[:end.x]])}
        self.quit_loop(0)


def main(args):
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


def handle_result(args, result, target_window_id, boss):
    if 'copy' in result:
        set_clipboard_string(result['copy'])
