from kitty.conf.types import Action, Definition

definition = Definition(
    '!kitten_options_utils',
    Action(
        'map', 'parse_map',
        {'key_definitions': 'kitty.conf.utils.KittensKeyMap'},
        ['kitty.types.ParsedShortcut', 'kitty.conf.utils.KeyAction']
    ),
)

agr = definition.add_group
egr = definition.end_group
opt = definition.add_option
map = definition.add_map

# color options {{{
agr('color', 'Color')

opt('selection_foreground', '#FFFFFF',
    option_type='to_color',
    long_text='''
Foreground color for selected text while grabbing.''')
opt('selection_background', '#5294E2',
    option_type='to_color',
    long_text='''
Background color for selected text while grabbing.''')

egr()  # }}}

# shortcuts {{{
agr('shortcuts', 'Keyboard shortcuts')

long_text = '''
Exit the grabber without copying anything.'''
map('Quit', 'quit q quit')
map('Quit', 'quit Escape quit', long_text=long_text)

long_text = '''
Copy the selected region to clipboard and exit.'''
map('Confirm', 'confirm Enter confirm', long_text=long_text)

long_text = '''
Cancel selection and move the cursor around the screen.
This will scroll the buffer if needed and possible.'''
map('Move left',           'move Left       move left')
map('Move right',          'move Right      move right')
map('Move up',             'move Up         move up')
map('Move down',           'move Down       move down')
map('Move page up',        'move Page_Up    move page up')
map('Move page down',      'move Page_Down  move page down')
map('Move first',          'move Home       move first')
map('Move first nonwhite', 'move a          move first nonwhite')
map('Move last nonwhite',  'move End        move last nonwhite')
map('Move last',           'move e          move last')
map('Move top',            'move Ctrl+Home  move top')
map('Move bottom',         'move Ctrl+End   move bottom')
map('Move word left',      'move Ctrl+Left  move word left')
map('Move word right',     'move Ctrl+Right move word right', long_text=long_text)

long_text = '''
Scroll the buffer, if possible.
Cursor stays in the same position relative to the screen.'''
map('Scroll up',   'scroll Ctrl+Up   scroll up')
map('Scroll down', 'scroll Ctrl+Down scroll down', long_text=long_text)

long_text = '''
#: Extend a stream selection.
#: If no region is selected, start selecting.
#: Stream selection includes all characters between the region ends.'''
map('SelectStream left',           'select_stream Shift+Left       select stream left')
map('SelectStream right',          'select_stream Shift+Right      select stream right')
map('SelectStream up',             'select_stream Shift+Up         select stream up')
map('SelectStream down',           'select_stream Shift+Down       select stream down')
map('SelectStream page up',        'select_stream Shift+Page_Up    select stream page up')
map('SelectStream page down',      'select_stream Shift+Page_Down  select stream page down')
map('SelectStream first',          'select_stream Shift+Home       select stream first')
map('SelectStream first nonwhite', 'select_stream A                select stream first nonwhite')
map('SelectStream last nonwhite',  'select_stream Shift+End        select stream last nonwhite')
map('SelectStream last',           'select_stream E                select stream last')
map('SelectStream top',            'select_stream Shift+Ctrl+Home  select stream top')
map('SelectStream bottom',         'select_stream Shift+Ctrl+End   select stream bottom')
map('SelectStream word left',      'select_stream Shift+Ctrl+Left  select stream word left')
map('SelectStream word right',     'select_stream Shift+Ctrl+Right select stream word right', long_text=long_text)

long_text = '''
Extend a columnar selection.
If no region is selected, start selecting.
Columnar selection includes characters in the rectangle
defined by the region ends.'''
map('SelectColumnar left',           'select_columnar Alt+Left       select columnar left')
map('SelectColumnar right',          'select_columnar Alt+Right      select columnar right')
map('SelectColumnar up',             'select_columnar Alt+Up         select columnar up')
map('SelectColumnar down',           'select_columnar Alt+Down       select columnar down')
map('SelectColumnar page up',        'select_columnar Alt+Page_Up    select columnar page up')
map('SelectColumnar page down',      'select_columnar Alt+Page_Down  select columnar page down')
map('SelectColumnar first',          'select_columnar Alt+Home       select columnar first')
map('SelectColumnar first nonwhite', 'select_columnar Alt+A          select columnar first nonwhite')
map('SelectColumnar last nonwhite',  'select_columnar Alt+End        select columnar last nonwhite')
map('SelectColumnar last',           'select_columnar Alt+E          select columnar last')
map('SelectColumnar top',            'select_columnar Alt+Ctrl+Home  select columnar top')
map('SelectColumnar bottom',         'select_columnar Alt+Ctrl+End   select columnar bottom')
map('SelectColumnar word left',      'select_columnar Alt+Ctrl+Left  select columnar word left')
map('SelectColumnar word right',     'select_columnar Alt+Ctrl+Right select columnar word right', long_text=long_text)

long_text = '''
Keys to enable vim-like modal selecting.'''
map('SetMode visual', 'set_mode v                   set_mode visual')
map('SetMode block',  'set_mode Ctrl+v              set_mode block')
map('SetMode normal', 'set_mode Ctrl+LeftBracket    set_mode normal', long_text=long_text)

egr()  # }}}

agr('behavior', 'Behavior')  # {{{

opt('select_by_word_characters', '',
    option_type='str',
    long_text='''
Characters considered part of a word when moving by words.
By default, those are taken from main Kitty config.''')

egr()  # }}}
