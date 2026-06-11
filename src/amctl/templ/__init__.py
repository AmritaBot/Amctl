import importlib
import pkgutil

from amctl.colors import ColorLog
from amctl.templating import BaseTemplate

__all__ = []

for loader, module_name, is_pkg in pkgutil.iter_modules(__path__):
    module = importlib.import_module(f"{__name__}.{module_name}")
    if (export := getattr(module, "__template_export__", None)) is None and getattr(
        module, "__template_ignore__", False
    ):
        raise ValueError(
            f"Module {module_name} does not have `__template_export__: BaseTemplate` attribute"
        )
    if not isinstance(export, type):
        raise TypeError(
            f"Module {module_name} has `__template_export__` attribute that is not a type"
        )
    if not issubclass(export, BaseTemplate):
        raise TypeError(
            f"Module {module_name} has `__template_export__` attribute that is not a subclass of BaseTemplate"
        )
    export.module = module
    ColorLog.success(f"Loaded template {export.__template_name__}")
    globals()[module_name] = module
    __all__.append(module_name)  # type: ignore # noqa: PYI056
