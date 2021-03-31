from pathlib import Path

import pandas as pd
import yaml

YAML_PATH = Path(r'colors.yaml')

def get_colors(path: Path = YAML_PATH) -> pd.DataFrame:
    with path.open('r') as file:
        colors = yaml.load(file, Loader=yaml.SafeLoader)
    df = pd.DataFrame(colors).transpose()
    df[['red', 'green', 'blue']] = df.iloc[:, 0].to_list()
    df = df.drop('dec_rgb', axis=1)
    return df
