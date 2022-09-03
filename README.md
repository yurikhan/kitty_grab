# Keyboard-driven screen grabber for Kitty

[Kitty][kitty] is a fast GPU-based terminal emulator.

[kitty]: https://sw.kovidgoyal.net/kitty/

Kitty lets you select text in the terminal using your mouse
and copy it to the clipboard using a key shortcut.
However, it lacks a built-in way to select text using the keyboard.

This project implements keyboard-driven text selection as a kitten.


# Minimum requirements

Kitty ≥0.21.2.

For Kitty ≥0.13.0, <0.21.0, see the tag `v0.20`,
but be aware that version will not be updated.


# Installation and initial configuration

* Clone this repository into your Kitty configuration directory:

      $ cd ~/.config/kitty
      $ git clone https://github.com/yurikhan/kitty_grab.git

* In the Kitty configuration file (`kitty.conf`),
  map a key to run the `grab.py` kitten:

      map Alt+Insert kitten kitty_grab/grab.py

* Restart kitty or reload the config (`Ctrl`+`Shift`+`F5` by default, see [kitty.conf](https://sw.kovidgoyal.net/kitty/conf/#shortcut-kitty.Reload-kitty.conf)).


# Usage

When you press the key bound to `kitten grab1.py`,
your screen will briefly flash
and its title will change to indicate the grabber is active.

You can now move your cursor around the screen using arrow keys.
It will scroll if you try to go beyond the screen top or bottom.
Hold down `Shift` while moving to select a stream region,
or `Alt` to select a rectangular (columnar) region.
Press `Enter` to copy the selected region to the clipboard and exit,
or `Esc` or `q` to exit without copying.


## Start/end of buffer

`Ctrl`+`Home`/`End` move (or, with `Shift` or `Alt`, select)
to the top left or bottom right of the buffer, respectively.

**Note:** By default, Kitty binds `Ctrl`+`Shift`+`Home`/`End`
to scroll the scrollback buffer to top and bottom, respectively.
You might want to install [`kitty_scroll`][kitty_scroll]
to be able to use these shortcuts with `kitty_grab`.

[kitty_scroll]: https://github.com/yurikhan/kitty-smart-scroll

    map Ctrl+Shift+Home  kitten smart_scroll.py scroll_home Ctrl+Shift+Home
    map Ctrl+Shift+End   kitten smart_scroll.py scroll_end  Ctrl+Shift+End


## Word motion

Hold down `Ctrl` while pressing `←`/`→` to move by words.


**Note:** By default, Kitty binds `Ctrl`+`Shift`+`←`/`→`
to activate the previous/next tab.
That will prevent `kitty_grab`,
as well as other terminal-based programs,
from seeing these combinations.
You can either bind different keys in `grab.conf`:

    map Shift+Alt+B  select stream word left
    map Shift+Alt+F  select stream word right

or rebind previous/next tab to different keys in `kitty.conf`
(recommended):

    map kitty_mod+Left   no_op
    map kitty_mod+Right  no_op
    map Ctrl+Page_Up     previous_tab
    map Ctrl+Page_Down   next_tab

(Remember to [reload config](https://sw.kovidgoyal.net/kitty/conf/#shortcut-kitty.Reload-kitty.conf/) if you modify `kitty.conf`.)


# Configuration

See the `grab.conf.example` file.
You will need to copy it to `~/.config/kitty/grab.conf`
and edit to your liking.

All example entries are commented out.
Remove the `#` at the start of lines you modify.

You do not need to reload config when you edit `grab.conf`.
It will take effect the next time you use the grabber.


# Vim-like Modal Highlighting

Vim-like modal selecting is available.
Copy the provided `grab-vim.conf.example` file, and copy it to `~/.config/kitty/grab.conf`.


# License

GNU Public License version 3 or later.
