import argparse

from storage import DB_PATH, initialize_database


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize the new Pelada migration SQLite database."
    )
    parser.add_argument(
        "--confirm-create",
        action="store_true",
        help="confirm creation or validation of the migration database",
    )
    arguments = parser.parse_args()

    if not arguments.confirm_create:
        parser.error("initialization requires the explicit --confirm-create flag")

    database_path = initialize_database()
    print(f"Migration database initialized and verified at: {database_path}")


if __name__ == "__main__":
    main()
