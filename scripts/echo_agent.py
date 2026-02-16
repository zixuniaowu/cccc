import sys

def main():
    for line in sys.stdin:
        sys.stdout.write(f"[echo] {line}")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
