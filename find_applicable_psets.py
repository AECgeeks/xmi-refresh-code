import argparse
import glob
from pathlib import Path
from xml.etree import ElementTree as ET


def applicable_values(path):
    text = ET.parse(path).getroot().findtext("ApplicableTypeValue") or ""
    return [item.strip() for item in text.split(",") if item.strip()]


def matches(values, targets):
    return any(value == target or value.startswith(target + "/")
               for target in targets for value in values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", help="XML glob or directory")
    parser.add_argument("applicable_type", nargs="+")
    args = parser.parse_args()

    pattern = str(Path(args.files) / "*.xml") if Path(args.files).is_dir() else args.files
    targets = args.applicable_type

    for file in sorted(glob.glob(pattern, recursive=True)):
        path = Path(file)
        if path.is_file() and matches(applicable_values(path), targets):
            print(path.name)


if __name__ == "__main__":
    main()
