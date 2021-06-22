from typing import Any, Sequence, Tuple, Iterable

from kitty.conf.utils import KittensKeyDefinition, key_func, parse_kittens_key
from kitty.rgb import Color, parse_sharp

func_with_args, args_funcs = key_func()
FuncArgsType = Tuple[str, Sequence[Any]]


def color_parser(val: str) -> Color:
    assert val.startswith('#'), f"Color should start with #, got {val}"
    color = parse_sharp(val[1:])
    if color is None:
        raise ValueError(f"Couldn't parse {val} as a color")
    return color


def parse_map(val: str) -> Iterable[KittensKeyDefinition]:
    x = parse_kittens_key(val, args_funcs)
    if x is not None:
        yield x


# TODO what are these functions supposed to do?
@func_with_args('move')
def move(func, rest):
    return func, rest


@func_with_args('scroll')
def scroll(func, rest):
    return func, rest


@func_with_args('select')
def select(func, rest):
    return func, rest
