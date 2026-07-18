from agent import run_agent


def main():
    print("=== Helio AI (Tool-Enabled) ===")
    print("Type 'exit' to quit\n")

    while True:
        query = input("You: ")

        if query.lower() in ["exit", "quit"]:
            print("Helio: Goodbye")
            break

        print("\nHelio: Thinking...\n")

        answer = run_agent(query)

        print(answer)
        print("\n" + "-" * 50 + "\n")


if __name__ == "__main__":
    main()
