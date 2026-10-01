import argparse


def count_words(text):
    return len(text.split())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Count words in a file.")
    parser.add_argument("path")
    args = parser.parse_args(argv)
    with open(args.path) as f:
        print(count_words(f.read()))


if __name__ == "__main__":
    main()
