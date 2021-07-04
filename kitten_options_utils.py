from typing import Any, Sequence, Tuple, Iterable, Callable

from kitty.conf.utils import KittensKeyDefinition, key_func, parse_kittens_key

func_with_args, args_funcs = key_func()
FuncArgsType = Tuple[str, Sequence[Any]]


def parse_map(val: str) -> Iterable[KittensKeyDefinition]:
    x = parse_kittens_key(val, args_funcs)
    if x is not None:
        yield x


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
