"""
Helpers for added runtime capabilities of transpiled BuildDSL code, specifically around variable
name resolution when enabling #TranspileOptions.closure_target.
"""

import builtins
import enum
import sys
import types
import typing as t
from dataclasses import dataclass

import typing_extensions as te

from ._transpiler import TranspileOptions, transpile_to_ast

# import weakref


class NotSet(enum.Enum):
    Value = 1


undefined = NotSet.Value


class Context(te.Protocol):
    """
    A context implements the behaviour for dynamic name resolution in programs transpiled with
    BuildDSL (when dynamic name resolution is enabled). It behaves essentially as a mapping
    that is used to get/set/delete variables.
    """

    def __getitem__(self, key: str) -> t.Any:
        ...

    def __setitem__(self, key: str, value: t.Any) -> None:
        ...

    def __delitem__(self, key: str) -> None:
        ...

    def chain_with(self, other: "Context") -> "ChainContext":
        return ChainContext(self, other)


class ObjectContext(Context):
    """
    Proxies an object's members for get/set/delete operations of the dynamic name resolution.

    Raises a #RuntimeError if there is an attempted to set or delete an attribute that appears
    to be a method of the object.
    """

    def __init__(self, target: t.Any) -> None:
        self._target = target

    def _error(self, key: str) -> NameError:
        raise NameError(f"object of type {type(self._target).__name__} does not have an attribute {key!r}")

    def __getitem__(self, key: str) -> t.Any:
        value = getattr(self._target, key, undefined)
        if value is not undefined:
            return value
        raise self._error(key)

    def __setitem__(self, key: str, value: t.Any) -> None:
        current = getattr(self._target, key, undefined)
        if current is undefined:
            raise self._error(key)
        if isinstance(current, types.MethodType) and current.__self__ is current:
            raise RuntimeError(f"cannot overwrite method {type(self._target).__name__}.{key}()")
        setattr(self._target, key, value)

    def __delitem__(self, key: str) -> None:
        current = getattr(self._target, key, undefined)
        if current is undefined:
            raise self._error(key)
        if isinstance(current, types.MethodType) and current.__self__ is current:
            raise RuntimeError(f"cannot delete method {type(self._target).__name__}.{key}()")
        delattr(self._target, key)


class MapContext(Context):
    """
    Delegates dynamic name resolution to a mapping.
    """

    def __init__(self, target: t.MutableMapping[str, t.Any], description: str) -> None:
        self._target = target
        self._description = description

    def _error(self, key: str) -> NameError:
        raise NameError(f"{self._description} does not have an attribute {key!r}")

    def __getitem__(self, key: str) -> t.Any:
        if key in self._target:
            return self._target[key]
        raise self._error(key)

    def __setitem__(self, key: str, value: t.Any) -> None:
        if key in self._target:
            self._target[key] = value
        else:
            raise self._error(key)

    def __delitem__(self, key: str) -> None:
        if key in self._target:
            del self._target[key]
        else:
            raise self._error(key)


class ChainContext(Context):
    """
    Chain multiple #Context implementations.
    """

    def __init__(self, *contexts: Context) -> None:
        self._contexts = contexts

    def __getitem__(self, key: str) -> t.Any:
        for ctx in self._contexts:
            try:
                return ctx[key]
            except NameError:
                pass
        raise NameError(key)

    def __setitem__(self, key: str, value: t.Any) -> None:
        for ctx in self._contexts:
            try:
                ctx[key] = value
                return
            except NameError:
                pass
        raise NameError(key)

    def __delitem__(self, key: str) -> None:
        for ctx in self._contexts:
            try:
                del ctx[key]
                return
            except NameError:
                pass
        raise NameError(key)

    def chain_with(self, other: Context) -> "ChainContext":
        return ChainContext(*self._contexts, other)


class Closure(Context):
    r"""
    This class serves as a mapping to use for dynamic name lookup when transpiling BuildDSL code to Python.
    Several options in the #TranspileOptions need to be tweaked for this to work correctly as the closure
    hierarchy needs to be built up manually:

    * Set #TranspileOptions.closure_target to `__closure__`
    * Set #TranspileOptions.closure_def_prefix to `@__closure__.subclosure\n`
    * Set #TranspileOptions.closure_arglist_prefix to `__closure__,`

    You can initialize a #TranspileOptions object with these values using #init_options().

    When resolving names using #__getitem__(), #__setitem__() and #__delitem__(), the names will be looked
    up in the hierarchy of the closure itself. However do note that #__setitem__() and #__delitem__() cannot
    apply changes to the locals in a function. This is handled by proper rewriting rules in the #NameRewriter.
    """

    @staticmethod
    def init_options(options: TranspileOptions) -> None:
        options.closure_target = "__closure__"
        options.closure_def_prefix = "@__closure__.subclosure\n"
        options.closure_arglist_prefix = "__closure__,"

    @staticmethod
    def get_options() -> TranspileOptions:
        options = TranspileOptions()
        Closure.init_options(options)
        options.sync()
        return options

    def __init__(
        self,
        parent: t.Optional["Closure"] = None,
        frame: t.Optional[types.FrameType] = None,
        target: t.Any = None,
        context_factory: t.Callable[[t.Any], Context] = ObjectContext,
        target_context: t.Optional[Context] = None,
    ) -> None:
        """
        Args:
          parent: The parent context. Dynamic name resolution is delegated to the parent if the current
            context fails to provide it.
          frame: The Python frame in which the closure is defined.
          target: The target object that is wrapped by the closure.
          context_factory: A factory to create #Context#s for closures defined inside this closure.
          target_context: The context for this closure. If not given, it will be created using the
            *context_factory*.
        """

        self._parent = parent
        self._frame = frame  # weakref.ref(frame) if frame else None  # NOTE (@NiklasRosenstein): Cannot create weakref to frame  # noqa: E501
        self._target = target
        self._target_context = (
            (context_factory(target) if target is not None else None) if target_context is None else target_context
        )
        self._context_factory = context_factory

    def __repr__(self) -> str:
        return f"Closure(target={self._target!r})"

    @property
    def frame(self) -> t.Optional[types.FrameType]:
        return self._frame

    def subclosure(self, func: t.Callable[..., t.Any], frame: t.Optional[types.FrameType] = None) -> "UnboundClosure":
        if frame is None:
            frame = sys._getframe(1)

        closure = UnboundClosure(self, frame, func)
        del frame

        return closure

    def run_code(
        self,
        code: str,
        filename: str = "<string>",
        options: t.Optional[TranspileOptions] = None,
        scope: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> None:
        """
        Executes the given BuildDSL *code* with the #Closure as it's entry `__closure__` object.
        """

        if options:
            Closure.init_options(options)
        else:
            options = Closure.get_options()
        module = compile(transpile_to_ast(code, filename, options), filename, "exec")
        if scope is None:
            scope = {}
        assert options.closure_target
        scope[options.closure_target] = self
        exec(module, scope)

    def __getitem__(self, key: str) -> t.Any:
        frame = self.frame
        if frame and key in frame.f_locals:
            return frame.f_locals[key]
        if self._target_context is not None:
            try:
                return self._target_context[key]
            except NameError:
                pass
        if self._parent is not None:
            try:
                return self._parent[key]
            except NameError:
                pass
        if hasattr(builtins, key):
            return getattr(builtins, key)
        raise NameError(f"{key!r} in {self!r}")

    def __setitem__(self, key: str, value: t.Any) -> None:
        frame = self.frame
        if frame and key in frame.f_locals:
            raise RuntimeError("cannot set local variable through context, this should be handled by the transpiler")
        if self._target_context is not None:
            try:
                self._target_context[key] = value
                return
            except NameError:
                pass
        if self._parent is not None:
            try:
                self._parent[key] = value
                return
            except NameError:
                pass
        raise NameError(f"unclear where to set {key!r}")

    def __delitem__(self, key: str) -> None:
        frame = self.frame
        if frame and key in frame.f_locals:
            raise RuntimeError("cannot delete local variable through context, this should be handled by the transpiler")
        if self._target_context is not None:
            try:
                del self._target_context[key]
                return
            except NameError:
                pass
        if self._parent is not None:
            try:
                del self._parent[key]
                return
            except NameError:
                pass
        raise NameError(f"unclear where to delete {key!r}")

    @staticmethod
    def from_map(m: t.MutableMapping[str, t.Any]) -> "Closure":
        return Closure(None, None, m, ObjectContext, MapContext(m, "Closure.from_map()"))


@dataclass
class UnboundClosure:
    parent: Closure
    frame: types.FrameType
    func: t.Callable[..., t.Any]

    def __call__(self, *args: t.Any, **kwargs: t.Any) -> t.Any:
        __closure__ = Closure(self.parent, self.frame, args[0] if args else None, self.parent._context_factory)
        return self.func(__closure__, *args, **kwargs)
