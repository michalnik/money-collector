import asyncio
from .collector import (
    configuration_exists,
    configuration_read,
    configuration_setup,
    email_smtp,
    fakturoid,
    main,
)


def main_entry():
    if not configuration_exists():
        configuration_setup()

    config = configuration_read()
    fakturoid.set_from_config(config["fakturoid"])
    email_smtp.set_from_config(config["email"])

    asyncio.run(main())


if __name__ == "__main__":
    main_entry()
