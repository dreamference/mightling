import sys

from app import users


def main():
    print(users.get_usr(int(sys.argv[1])))
