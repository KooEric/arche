import sys
from tally import counts

if __name__ == "__main__":
    for tag, n in counts(sys.argv[1:]):
        print(tag, n)
