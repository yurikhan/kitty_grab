# Keyboard-driven screen grabber for Kitty

[Kitty][kitty] is a fast GPU-based terminal emulator.

[kitty]: https://sw.kovidgoyal.net/kitty/

Kitty lets you select text in the terminal using your mouse
and copy it to the clipboard using a key shortcut.
However, it lacks a built-in way to select text using the keyboard.

This project implements keyboard-driven text selection as a kitten.


# Installation and initial configuration

* Put this project’s files into your Kitty configuration directory
  (`~/.config/kitty`).

* In the Kitty configuration file (`kitty.conf`),
  map a key to run the `grab1.py` kitten:

      map Alt+Insert kitten grab1.py

* Restart Kitty.


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


# Configuration

See the `grab.conf.example` file.
You will need to copy it to `~/.config/kitty/grab.conf`
and edit to your liking.

All example entries are commented out.
Remove the `#` at the start of lines you modify.

You do not need to restart Kitty when you edit `grab.conf`.
It will take effect the next time you use the grabber.


# License

GNU Public License version 3 or later.
