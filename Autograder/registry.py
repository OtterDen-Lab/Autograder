import abc
import importlib
import pathlib
import pkgutil
from typing import Dict, Any, Optional, Callable, Type

import logging

log = logging.getLogger(__name__)


class BaseRegistry(abc.ABC):
  """
    Base class for registry pattern implementations.
    Provides common functionality for registering and creating instances.
    """
  # These will be overridden in subclasses
  _registry: Dict[str, type] = None
  _scanned: bool = None

  @classmethod
  def register(cls, type_name: Optional[str] = None) -> Callable:
    """
        Decorator for registering subclasses.
        
        Args:
            type_name: Optional name for the registered type. 
                      If not provided, uses the class name.
        """
    log.debug("Registering...")

    def decorator(subclass: Type) -> Type:
      # Use the provided name or fall back to the class name
      name = (type_name.lower() if type_name else subclass.__name__.lower())
      cls._registry[name] = subclass
      return subclass

    return decorator

  @classmethod
  def create(cls, type_name: str, **kwargs) -> Any:
    """
        Factory method to instantiate a registered subclass.
        
        Args:
            type_name: The name of the registered type to create
            **kwargs: Arguments to pass to the constructor
            
        Returns:
            Instance of the requested type
            
        Raises:
            ValueError: If the type_name is not registered
        """
    # If we haven't already loaded our premades, do so now
    if not cls._scanned:
      cls.load_premade_modules()

    # Check to see if it's in the registry
    if type_name.lower() not in cls._registry:
      raise ValueError(
        f"Unknown {cls.get_type_description()} type: {type_name}")

    return cls._registry[type_name.lower()](**kwargs)

  @classmethod
  def load_premade_modules(cls) -> None:
    """
        Load all modules from the appropriate subdirectory to trigger registration.
        Subclasses should override get_module_info() to specify the package and path.
        """
    package_name, relative_path = cls.get_module_info()
    package_path = pathlib.Path(__file__).parent / relative_path
    log.debug(f"Loading modules from package_path: {package_path}")

    if package_path.is_dir():
      # Load all modules from a package directory
      for _, module_name, _ in pkgutil.iter_modules([str(package_path)]):
        # Import the module to trigger registration decorators
        module = importlib.import_module(f"{package_name}.{module_name}")
        log.debug(f"Loaded module: {module}")
    else:
      # Load a single module file
      module = importlib.import_module(package_name)
      log.debug(f"Loaded module: {module}")

    cls._scanned = True

  @classmethod
  @abc.abstractmethod
  def get_module_info(cls) -> tuple[str, str]:
    """
        Return the package name and relative path for loading modules.
        
        Returns:
            Tuple of (package_name, relative_path)
        """
    pass

  @classmethod
  @abc.abstractmethod
  def get_type_description(cls) -> str:
    """
        Return a description of what this registry manages (for error messages).
        
        Returns:
            String description (e.g., "grader", "assignment")
        """
    pass


class GraderRegistry(BaseRegistry):
  """Registry for grader implementations."""
  _registry: Dict[str, type] = {}
  _scanned: bool = False

  @classmethod
  def get_module_info(cls) -> tuple[str, str]:
    """Return package info for loading grader modules."""
    return ("Autograder.graders", "graders")

  @classmethod
  def get_type_description(cls) -> str:
    """Return description for error messages."""
    return "grader"


class AssignmentRegistry(BaseRegistry):
  """Registry for assignment implementations."""
  _registry: Dict[str, type] = {}
  _scanned: bool = False

  @classmethod
  def get_module_info(cls) -> tuple[str, str]:
    """Return package info for loading assignment modules."""
    return ("Autograder", "assignment"
            )  # Assignment types are in assignment.py

  @classmethod
  def get_type_description(cls) -> str:
    """Return description for error messages."""
    return "assignment"


class TypeRegistry:
  """
    Registry for assignment type configurations from YAML.

    Manages assignment_types definitions that specify:
    - kind: The Assignment class to use (e.g., ProgrammingAssignment)
    - grader: The default Grader to use (e.g., template-grader)
    - settings: Default settings for this type
    """
  _types: Dict[str, Dict[str, Any]] = {}

  @classmethod
  def load_from_yaml(cls, yaml_config: Dict[str, Any]) -> None:
    """
        Load assignment type definitions from YAML config.

        Args:
            yaml_config: The full YAML configuration dict
        """
    cls._types = yaml_config.get('assignment_types', {})
    log.info(
      f"Loaded {len(cls._types)} assignment type(s): {list(cls._types.keys())}"
    )

  @classmethod
  def get_type_config(cls, type_name: str) -> Dict[str, Any]:
    """
        Get the configuration for a specific assignment type.

        Args:
            type_name: The name of the assignment type (e.g., 'programming', 'text')

        Returns:
            Dict containing 'kind', 'grader', and 'settings' for the type

        Raises:
            ValueError: If the type_name is not defined
        """
    if type_name not in cls._types:
      raise ValueError(f"Unknown assignment type: {type_name}. "
                       f"Available types: {list(cls._types.keys())}")
    return cls._types[type_name]

  @classmethod
  def merge_settings(cls, type_name: str, *override_dicts:
                     Dict[str, Any]) -> Dict[str, Any]:
    """
        Merge settings from type defaults with overrides.

        Args:
            type_name: The assignment type name
            *override_dicts: Variable number of dicts to merge, in priority order

        Returns:
            Merged settings dict
        """
    type_config = cls.get_type_config(type_name)

    # Start with type's default settings
    merged = type_config.get('settings', {}).copy()

    # Merge each override dict in order
    for override in override_dicts:
      if override:
        merged.update(override)

    return merged
