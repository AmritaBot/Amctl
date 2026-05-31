from abc import ABC, abstractmethod
from types import ModuleType
from typing import ClassVar

from amctl.colors import ColorLog


class BaseTemplate(ABC):
    """Base class for templates"""

    __template_name__: str
    __override__: bool = False  # Whether to allow overriding existing templs
    __abstract__: bool = (
        False  # Whether this class is abstract and should not be registered
    )
    __no_register__: bool = (
        False  # Whether to not register this templ, even if it's not abstract
    )
    module: ClassVar[
        ModuleType
    ]  # The module where this templ is defined, set by the importer

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if not getattr(cls, "__abstract__", False) and not getattr(
            cls, "__no_register__", False
        ):
            TemplateManager().register_templ(cls)

    @classmethod
    def get_template_name(cls) -> str:
        """Get template name"""
        return cls.__template_name__

    @abstractmethod
    def on_create(self, *args, **kwargs) -> None:
        """Called when a new project is created"""
        ...


class TemplateManager:
    __instance = None
    __inited = False
    _templ_class: dict[str, type[BaseTemplate]]

    def __new__(cls):
        if cls.__instance is None:
            cls.__instance = super().__new__(cls)
            cls.__instance._templ_class = {}
        return cls.__instance

    def __init__(self):
        if not self.__inited:
            super().__init__()
            self.__inited = True

    def get_templs(self) -> dict[str, type[BaseTemplate]]:
        """Get all registered templs"""
        return self._templ_class

    def safe_get_templ(self, tmpl_name: str) -> type[BaseTemplate] | None:
        """Get templ"""
        return self._templ_class.get(tmpl_name)

    def get_templ(self, tmpl_name: str) -> type[BaseTemplate]:
        """Get templ"""
        if tmpl_name not in self._templ_class:
            raise ValueError(f"No templ found for tmpl_name {tmpl_name}")
        return self._templ_class[tmpl_name]

    def register_templ(self, templ: type[BaseTemplate]):
        """Register templ"""
        tmpl_name = templ.get_template_name()
        override = templ.__override__ if hasattr(templ, "__override__") else False
        if isinstance(tmpl_name, str):
            if tmpl_name in self._templ_class:
                if not override:
                    raise ValueError(
                        f"Project template templ {tmpl_name} is already registered"
                    )
                ColorLog.warn(
                    f"Project template templ {tmpl_name} has been registered by {self._templ_class[tmpl_name].__name__}, overriding existing templ"
                )

            self._templ_class[tmpl_name] = templ
