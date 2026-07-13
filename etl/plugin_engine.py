import importlib
import pkgutil
from pathlib import Path
import pandas as pd

BASE_PACKAGE = "etl.transformers"
BASE_PATH = Path(__file__).resolve().parent / "transformers"

def discover_plugins_for_table(table_name: str) -> list[str]:
    """
    Auto-discover plugins under etl/transformers/{table_name}
    Return module names sorted by module file name.
    """
    table_path = BASE_PATH / table_name
    if not table_path.exists() or not table_path.is_dir():
        return []

    module_names = []
    for m in pkgutil.iter_modules([str(table_path)]):
        if m.ispkg:
            continue
        # skip private files
        if m.name.startswith("_"):
            continue
        module_names.append(f"{BASE_PACKAGE}.{table_name}.{m.name}")

    # sort by module short name -> supports 01_xxx, 02_xxx ordering
    module_names.sort(key=lambda x: x.split(".")[-1])
    return module_names

def run_plugins(df: pd.DataFrame, module_names: list[str]) -> pd.DataFrame:
    out = df.copy()
    for module_name in module_names:
        module = importlib.import_module(module_name)
        if not hasattr(module, "transform"):
            raise AttributeError(f"{module_name} missing transform(df) function")
        out = module.transform(out)
    return out