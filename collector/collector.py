import re
import smtplib
import tomllib
import typing
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

import httpx
from InquirerPy import inquirer, prompt
from InquirerPy.base.control import Choice
from InquirerPy.validator import EmptyInputValidator
from prompt_toolkit.validation import ValidationError, Validator


class NoClientsFound(Exception):
    pass


@dataclass
class Client:
    ID: str = ""
    SECRET: str = ""
    APP_NAME: str = ""
    EMAIL: str = ""
    ACCOUNT: str = ""
    BASE_URL: str = "https://app.fakturoid.cz/api/v3"

    ends: dict[str, str] = field(default_factory=dict)
    token: str = ""

    def set_from_config(self, configuration: dict[str, typing.Any]):
        self.ID = configuration["client_id"]
        self.SECRET = configuration["client_secret"]
        self.APP_NAME = configuration["application_name"]
        self.EMAIL = configuration["email"]
        self.ACCOUNT = configuration["account"]

        self.ends = {
            "token": "/oauth/token",
            "user": "/user.json",
            "subjects": f"/accounts/{self.ACCOUNT}/subjects.json",
            "invoices": f"/accounts/{self.ACCOUNT}/invoices.json",
            "invoice_actions": f"/accounts/{self.ACCOUNT}/invoices/%d/fire.json",
            "payments": f"/accounts/{self.ACCOUNT}/invoices/%d/payments.json",
            "download_pdf": f"/accounts/{self.ACCOUNT}/invoices/%d/download.pdf",
        }

    @property
    def user_agent(self) -> str:
        return f"{self.APP_NAME} ({self.EMAIL})"

    @property
    def headers(self) -> dict[str, str]:
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        else:
            headers["Authorization"] = "Basic " + b64encode(f"{self.ID}:{self.SECRET}".encode("utf-8")).decode("ascii")
        return headers

    async def authenticate(self):
        data = {"grant_type": "client_credentials"}
        async with httpx.AsyncClient() as client:
            res = await client.post(f"{self.BASE_URL}{self.ends['token']}", headers=self.headers, data=data)
            res.raise_for_status()
            self.token = res.json()["access_token"]

    async def request(  # noqa[C901] McCabe[8]
        self,
        method: str,
        endpoint: str,
        url_params: tuple | None = None,
        query_params: dict | None = None,
        data: dict | list | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict | list[dict] | bytes:
        if not self.token:
            await self.authenticate()
        url_path = self.ends[endpoint]
        if url_params is not None:
            url_path %= url_params
        url: str = f"{self.BASE_URL}{url_path}"
        options: dict = {"headers": self.headers}
        if query_params is not None:
            options["params"] = query_params
        if data is not None:
            options["json"] = data
        if headers is not None:
            options["headers"].update(headers)
        async with httpx.AsyncClient() as client:
            res = await client.request(method, url, **options)
            res.raise_for_status()
            if res.headers.get("Content-Transfer-Encoding", None) == "binary":
                return res.content
            if res.status_code == 204:  # no content
                return {"status": "ok", "message": "No content"}
            return res.json()


fakturoid = Client()


@dataclass
class Email:
    SMTP_SERVER: str = ""
    SMTP_PORT: int = -1
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SUBJECT: str = "Faktura č. MM%s"
    BODY: str = "Hezký den,\n\nVystavil jsem pro Vás fakturu.\n\nDíky!\n\n%s"

    def set_from_config(self, configuration: dict[str, typing.Any]):
        self.SMTP_SERVER = configuration["smtp_server"]
        self.SMTP_PORT = configuration["smtp_port"]
        self.SMTP_USER = configuration["smtp_user"]
        self.SMTP_PASSWORD = configuration["smtp_password"]

    def send_message(self, message: EmailMessage):
        with smtplib.SMTP_SSL(self.SMTP_SERVER, self.SMTP_PORT) as smtp:
            smtp.login(self.SMTP_USER, self.SMTP_PASSWORD)
            smtp.send_message(message)


email_smtp = Email()


async def get_subjects() -> list[dict]:
    subjects: list[dict] = []
    for c in await fakturoid.request("get", "subjects"):
        assert isinstance(c, dict)
        subjects.append({"id": c["id"], "name": c["name"], "email": c["email"], "regno": c["registration_no"]})
    return subjects


async def select_client() -> dict[str, typing.Any]:
    _clients: list[dict] = await get_subjects()
    choices = [f"{c['id']} - {c['name']}" for c in _clients]
    if not choices:
        raise NoClientsFound()
    fuzzy = inquirer.fuzzy(
        message="Choose your client for invoicing:",
        choices=choices,
    )
    selected = typing.cast(str, await fuzzy.execute_async())
    selected_id = int(selected.split(" - ")[0])
    return [c for c in _clients if c["id"] == selected_id][0]


async def issue_invoice(
    selected_client: dict, invoice_issued_date: date, due: int, items: list[dict]
) -> dict[str, typing.Any]:
    for item in items:
        item.pop("total_price")

    data = {
        "subject_id": selected_client["id"],
        "issued_on": invoice_issued_date.isoformat(),
        "due": int(due),
        "lines": items,
    }
    res = await fakturoid.request("post", "invoices", data=data)
    assert isinstance(res, dict)
    return res


async def download_pdf(invoice_id: int) -> bytes:
    pdf_content = await fakturoid.request("get", "download_pdf", url_params=(invoice_id,))
    assert isinstance(pdf_content, bytes)
    return pdf_content


class DateValidator(Validator):
    def validate(self, document):
        try:
            datetime.strptime(document.text, "%Y-%m-%d")
        except ValueError:
            raise ValidationError(message="Invalid date, use YYYY-MM-DD", cursor_position=len(document.text))


async def get_date(date_type_text: str = "a general type") -> date:
    answer = inquirer.text(
        message=f"Enter {date_type_text} date (YYYY-MM-DD):",
        validate=DateValidator(),
    )
    date_str = typing.cast(str, await answer.execute_async())
    return datetime.strptime(date_str, "%Y-%m-%d").date()


async def add_item(invoice_issued_date: date) -> dict[str, typing.Any]:
    answer_float = inquirer.number(
        message="Enter count of units:",
        float_allowed=True,
        validate=EmptyInputValidator(),
    )
    units = typing.cast(str, await answer_float.execute_async())

    answer_text = inquirer.text(
        message="Enter name of unit:",
        completer={
            "hod": None,
            "MD": None,
            "měsíc": None,
            "kus": None,
        },
        multicolumn_complete=False,
    )
    unit_name = typing.cast(str, await answer_text.execute_async())

    answer_float = inquirer.number(
        message="Enter unit price:",
        float_allowed=True,
        validate=EmptyInputValidator(),
    )
    unit_price = typing.cast(str, await answer_float.execute_async())

    answer_text = inquirer.text(message="Enter item description:", default="Softwarové inženýrství v rámci projektu: ")
    description = typing.cast(str, await answer_text.execute_async())

    return {
        "quantity": float(units),
        "name": f"{description} za {invoice_issued_date.strftime('%m/%Y')}",
        "unit_name": unit_name,
        "unit_price": float(unit_price),
        "total_price": float(units) * float(unit_price),
    }


def send_email_with_invoice(pdf_content: bytes, subject_email: str, cc_email: str, subject: str, body: str):
    msg = EmailMessage()
    msg["From"] = email_smtp.SMTP_USER
    msg["To"] = subject_email
    if cc_email:
        msg["Cc"] = cc_email
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(pdf_content, maintype="application", subtype="pdf", filename="invoice.pdf")

    email_smtp.send_message(msg)


async def get_user() -> dict:
    user = await fakturoid.request("get", "user")
    assert isinstance(user, dict)
    return user


async def mark_invoice_as_sent(invoice_id: int):
    await fakturoid.request("POST", "invoice_actions", url_params=(invoice_id,), data={"event": "mark_as_sent"})


async def get_unsent_invoices(subject_id: int) -> list[dict]:
    invoices_unsent = await fakturoid.request(
        "get", "invoices", query_params={"subject_id": subject_id, "status": "open"}
    )
    assert isinstance(invoices_unsent, list)
    return invoices_unsent


async def get_unpaid_invoices(subject_id: int) -> list[dict]:
    invoices_unpaid: list[dict] = []
    for status in ["open", "sent", "overdue"]:
        invoices = await fakturoid.request("get", "invoices", query_params={"subject_id": subject_id, "status": status})
        assert isinstance(invoices, list)
        invoices_unpaid += invoices
    return invoices_unpaid


async def paid_invoice(invoice_id: int, paid_at: date):
    await fakturoid.request(
        "post",
        "payments",
        url_params=(invoice_id,),
        data={"paid_on": paid_at.strftime("%Y-%m-%d")},
    )


def to_camel_case(text: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", text.strip())
    parts = [part for part in parts if part]
    if not parts:
        return ""
    return "".join(word.capitalize() for word in parts)


class EmailValidator(Validator):
    def validate(self, document) -> None:
        try:
            match = re.match(r"^[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*@[^@\s]+\.[^@\s]+$", document.text)
            assert bool(match) is True
        except AssertionError:
            raise ValidationError(message="Invalid email address", cursor_position=len(document.text))


class ApplicationNameValidator(Validator):
    def __init__(self, transform: typing.Callable[[str], str]):
        super().__init__()
        self._transform = transform

    def validate(self, document) -> None:
        text = self.transform(document.text)
        if len(text) == 0:
            raise ValidationError(
                message="Application name cannot be empty.",
                cursor_position=len(document.text),
            )
        if len(text) > 40:
            raise ValidationError(
                message="Maximum length of application name is 40 characters.",
                cursor_position=len(document.text),
            )

    def transform(self, text: str) -> str:
        return self._transform(text)


APP_DIR = Path.home() / ".config" / "money-collector"
CONFIG_PATH = APP_DIR / "config.ini"


CONFIG_QUESTIONS = [
    {
        "type": "input",
        "name": "email",
        "message": "Entry your Fakturoid email: ",
        "validate": EmailValidator(),
    },
    {
        "type": "input",
        "name": "account",
        "message": "Entry your Fakturoid account name: ",
        "validate": EmptyInputValidator(),
    },
    {
        "type": "input",
        "name": "application_name",
        "message": "Entry your Fakturoid application name: ",
        "validate": ApplicationNameValidator(transform=to_camel_case),
        "filter": to_camel_case,
    },
    {
        "type": "input",
        "name": "client_id",
        "message": "Entry your Fakturoid client ID: ",
        "validate": EmptyInputValidator(),
    },
    {
        "type": "password",
        "name": "client_secret",
        "message": "Entry your Fakturoid client secret: ",
        "validate": EmptyInputValidator(),
    },
    {
        "type": "input",
        "name": "smtp_user",
        "message": "Entry your email SMTP user: ",
        "validate": EmptyInputValidator(),
    },
    {
        "type": "password",
        "name": "smtp_password",
        "message": "Entry your email SMTP password: ",
        "validate": EmptyInputValidator(),
    },
    {
        "type": "input",
        "name": "smtp_server",
        "message": "Entry your email SMTP server: ",
        "validate": EmptyInputValidator(),
    },
    {
        "type": "input",
        "name": "smtp_port",
        "message": "Entry your email SMTP server port:",
        "validate": lambda port: (port.isdigit() and 1 <= int(port) <= 65535) or "Entry valid port number (1–65535)!",
        "filter": lambda port: int(port),
    },
]


def configuration_exists() -> bool:
    if CONFIG_PATH.exists():
        return True
    return False


def configuration_setup():
    questions = CONFIG_QUESTIONS.copy()
    answers = prompt(questions)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        f.write("[fakturoid]\n")
        f.write(f'application_name = "{answers["application_name"]}"\n')
        f.write(f'email = "{answers["email"]}"\n')
        f.write(f'account = "{answers["account"]}"\n')
        f.write(f'client_id = "{answers["client_id"]}"\n')
        f.write(f'client_secret = "{answers["client_secret"]}"\n\n')

        f.write("[email]\n")
        f.write(f'smtp_user = "{answers["smtp_user"]}"\n')
        f.write(f'smtp_password = "{answers["smtp_password"]}"\n')
        f.write(f'smtp_server = "{answers["smtp_server"]}"\n')
        f.write(f'smtp_port = {answers["smtp_port"]}\n\n')


def configuration_read():
    with open(CONFIG_PATH, "rb") as f:
        configuration = tomllib.load(f)
        return configuration


async def main():  # noqa[C901] McCabe:6
    try:
        selected_client: dict = await select_client()
    except NoClientsFound:
        print("You have no clients to invoice.")
        return
    answer_bool = inquirer.confirm(message="Create invoice?", default=True)
    if typing.cast(bool, await answer_bool.execute_async()):
        invoice_issued_date: date = await get_date(date_type_text="an invoice issued")

        answer_number = inquirer.number(
            message="Enter an due of the issued invoice:",
            min_allowed=1,
            max_allowed=31,
            validate=EmptyInputValidator(),
        )
        due = typing.cast(int, await answer_number.execute_async())

        items = []
        answer_bool = inquirer.confirm(message="Add another item?", default=True)
        while typing.cast(bool, await answer_bool.execute_async()):
            item = await add_item(invoice_issued_date)
            items.append(item)

            print(f"Item description: {items[-1]['name']}")
            print(f"Total item price is: {items[-1]['total_price']} Kč")

        answer_bool = inquirer.confirm(message="Issue invoice?", default=True)
        if typing.cast(bool, await answer_bool.execute_async()):
            await issue_invoice(selected_client, invoice_issued_date, due, items)
    answer_bool = inquirer.confirm(message="Sent issued invoices?", default=False)
    if typing.cast(bool, await answer_bool.execute_async()):
        invoices_unsent = await get_unsent_invoices(selected_client["id"])
        if not invoices_unsent:
            print("No invoices to sent.")
        else:
            answer_select = inquirer.select(
                multiselect=True,
                message="Select invoices to sent:",
                choices=[Choice(name=invoice["number"], value=invoice["id"]) for invoice in invoices_unsent],
            )
            selected_choices = typing.cast(list, await answer_select.execute_async())

            selected_invoices = [invoice for invoice in invoices_unsent if invoice["id"] in selected_choices]
            for invoice in selected_invoices:
                invoice_pdf: bytes = await download_pdf(invoice["id"])
                user: dict = await get_user()
                send_email_with_invoice(
                    invoice_pdf,
                    selected_client["email"],
                    email_smtp.SMTP_USER,
                    email_smtp.SUBJECT % (invoice["number"],),
                    email_smtp.BODY % (user["full_name"],),
                )
                await mark_invoice_as_sent(invoice["id"])

    answer_bool = inquirer.confirm(message="Pay unpaid invoices?", default=True)
    if typing.cast(bool, await answer_bool.execute_async()):
        invoices_unpaid = await get_unpaid_invoices(selected_client["id"])
        if not invoices_unpaid:
            print("No invoices to pay.")
        else:
            answer_select = inquirer.select(
                multiselect=True,
                message="Select invoices to pay:",
                choices=[Choice(name=invoice["number"], value=invoice["id"]) for invoice in invoices_unpaid],
            )
            selected_choices = typing.cast(list, await answer_select.execute_async())

            selected_invoices = [invoice for invoice in invoices_unpaid if invoice["id"] in selected_choices]
            for invoice in selected_invoices:
                invoice["paid_at"] = await get_date(date_type_text=f'an invoice {invoice["number"]} paid')
                await paid_invoice(invoice["id"], invoice["paid_at"])
                print(f"Invoice {invoice['number']} was paid at {invoice['paid_at']}!")
