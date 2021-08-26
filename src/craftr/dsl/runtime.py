
import abc
import builtins
import contextlib
import functools
import sys
import threading
import typing as t
from itertools import chain

from .closure import Closure, ClosureContextProxy, ResolveStrategy
from .macros import MacroPlugin
from .transpiler import compile_file

T = t.TypeVar('T')
T_Callable = t.TypeVar('T_Callable', bound=t.Callable)


class Configurable(metaclass=abc.ABCMeta):

  @abc.abstractmethod
  def configure(self, closure: t.Callable[[t.Any], t.Any]) -> t.Any:
    pass


class NameProvider(metaclass=abc.ABCMeta):

  @abc.abstractmethod
  def _lookup_name(self, name: str) -> t.Any:
    pass


class PropertyOwner(metaclass=abc.ABCMeta):

  @abc.abstractmethod
  def _set_property_value(self, name: str, value: t.Any) -> None:
    pass


class Runtime:
  """
  A runtime object supports the execution of a Python module transpiled from the craftr DSL. The
  runtime is a thread-safe object that keeps track of the closure targets and local variables in
  order to implement the name resolution.
  """

  def closure(self, owner: t.Any, delegate: t.Any) -> t.Callable[[T_Callable], T_Callable]:
    """
    Decorator for closures.
    """

    def decorator(func):
      return Closure(func, sys._getframe(1), owner, delegate, ResolveStrategy.DELEGATE_FIRST)

    return decorator


def run_file(
  delegate: t.Any,
  globals: t.Dict[str, t.Any],
  filename: str,
  fp: t.Optional[t.TextIO] = None,
  macros: t.Optional[t.Dict[str, MacroPlugin]] = None,
) -> None:

  def _inner(self):
    globals['self'] = self
    module = compile_file(filename, fp, macros)
    code = compile(module, filename=filename, mode='exec')
    exec(code, globals)

  globals['__runtime__'] = Runtime()
  closure = Closure(_inner, sys._getframe(1), None, delegate)
  closure()


__all__ = ['NameProvider', 'PropertyOwner', 'Runtime', 'run_file']
