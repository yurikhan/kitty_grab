try:
    import grab2
    grab2_path = grab2.__file__
except (ImportError, AttributeError):
    grab2_path = 'grab2.py'

def main(args):
    pass


def handle_result(args, result, target_window_id, boss):
    window = boss.window_id_map.get(target_window_id)
    if window is None:
        return
    tab = window.tabref()
    if tab is None:
        return
    content = window.as_text(as_ansi=True, add_history=True,
                             add_wrap_markers=True)
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    n_lines = content.count('\n')
    top_line = (n_lines - (window.screen.lines - 1) - window.screen.scrolled_by)
    boss._run_kitten(grab2_path, args=[
        '--title={}'.format(window.title),
        '--cursor-x={}'.format(window.screen.cursor.x),
        '--cursor-y={}'.format(window.screen.cursor.y),
        '--top-line={}'.format(top_line)],
        input_data=content.encode('utf-8'),
        window=window)


handle_result.no_ui = True
