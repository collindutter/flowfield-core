"""Harbor command entry; features arrive through tracked tasks."""

import argparse


def main():
    parser = argparse.ArgumentParser(description="Offline reading catalog")
    parser.add_argument("--about", action="store_true")
    args = parser.parse_args()
    if args.about:
        print("Harbor: offline reading catalog")


if __name__ == "__main__":
    main()
