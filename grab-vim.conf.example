# vim:fileencoding=utf-8:ft=conf:foldmethod=marker

#: Colors {{{

# selection_foreground #FFFFFF
# selection_background #5294E2

#: Colors for selected text while grabbing.

# cursor #ad7fa8

#: Cursor color while grabbing.

#: }}}

#: Key shortcuts {{{

# map q      quit

#: Exit the grabber without copying anything.

# map Enter confirm
map y confirm

#: Copy the selected region to clipboard and exit.

map h           move left
map l           move right
map k           move up
map j           move down
map Ctrl+u      move page up
map Ctrl+d      move page down
map 0           move first
map ^           move first nonwhite
map $           move last nonwhite
map g           move top
map G           move bottom
map b           move word left
map w           move word right

#: Move the cursor around the screen.
#: This will scroll the buffer if needed and possible.
#: Note that due to https://github.com/kovidgoyal/kitty/issues/5469, the ctrl+d
#: shortcut will only work with kitty >= 0.26.2

map Ctrl+y scroll up
map Ctrl+e scroll down

#: Scroll the buffer, if possible.
#: Cursor stays in the same position relative to the screen.

map v                 set_mode visual
map Ctrl+v            set_mode block
map Ctrl+Left_Bracket set_mode normal
map Escape            set_mode normal

#: Change the selecting mode.

#: }}}
