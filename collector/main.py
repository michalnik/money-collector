import os
import requests
import datetime
import smtplib
from email.message import EmailMessage

FAKTUROID_EMAIL = os.environ["FAKTUROID_EMAIL"]
FAKTUROID_TOKEN = os.environ["FAKTUROID_TOKEN"]
FAKTUROID_ACCOUNT = os.environ["FAKTUROID_ACCOUNT"]
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]

HEADERS = {
    "Content-Type": "application/json",
}


def fakturoid_request(method, endpoint, data=None):
    url = f"https://app.fakturoid.cz/api/v3/accounts/{FAKTUROID_ACCOUNT}/{endpoint}"
    auth = (FAKTUROID_EMAIL, FAKTUROID_TOKEN)
    response = requests.request(method, url, headers=HEADERS, auth=auth, json=data)
    response.raise_for_status()
    return response.json() if response.text else {}


def get_clients():
    clients = fakturoid_request("GET", "subjects.json")
    return clients


def create_invoice(client_id, issue_date, due_date, items):
    data = {"subject_id": client_id, "issued_on": issue_date, "due": due_date, "lines": items}
    invoice = fakturoid_request("POST", "invoices.json", data)
    return invoice


def get_unpaid_invoices():
    invoices = fakturoid_request("GET", "invoices.json?state=open")
    return invoices


def mark_invoice_paid(invoice_id, paid_date):
    data = {"event": "paid", "paid_at": paid_date}
    fakturoid_request("POST", f"invoices/{invoice_id}/fire.json", data)


def send_email_with_invoice(pdf_content, recipient_email, cc_email, subject, body):
    msg = EmailMessage()
    msg["From"] = SMTP_USER
    msg["To"] = recipient_email
    if cc_email:
        msg["Cc"] = cc_email
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(pdf_content, maintype="application", subtype="pdf", filename="invoice.pdf")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)


def main():  # noqa[C903]
    clients = get_clients()
    print("\nDostupní klienti:")
    for idx, c in enumerate(clients):
        print(f"{idx + 1}. {c['name']} ({c['email']})")

    sel = int(input("\nVyber číslo klienta: ")) - 1
    client = clients[sel]
    issue_date = input("Datum vystavení faktury (YYYY-MM-DD, prázdné = dnes): ")
    if not issue_date:
        issue_date = str(datetime.date.today())
    due_date = 14

    qty = float(input("Počet jednotek: "))
    unit_name = input("Název jednotky: ")
    description = input("Popis fakturované položky: ")
    unit_price = float(input("Cena za jednotku: "))
    total_price = qty * unit_price
    print(f"Celkem: {total_price} Kč")

    confirm = input("Potvrdit vystavení faktury? (y/n): ")
    if confirm.lower() != "y":
        print("Zrušeno.")
        return

    items = [{"name": description, "quantity": qty, "unit_name": unit_name, "unit_price": unit_price}]

    invoice = create_invoice(client["id"], issue_date, due_date, items)
    invoice["id"]
    print(f"Faktura vystavena: {invoice['number']}")

    cc_email = input("Zadat email pro kopii (prázdné = žádná): ")

    pdf_content = requests.get(invoice["urls"]["pdf"], auth=(FAKTUROID_EMAIL, FAKTUROID_TOKEN)).content
    send_email_with_invoice(
        pdf_content, client["email"], cc_email, f"Faktura {invoice['number']}", "Dobrý den, zasíláme fakturu v příloze."
    )
    print("Faktura odeslána emailem.")

    unpaid = get_unpaid_invoices()
    if not unpaid:
        print("Žádné nezaplacené faktury.")
        return

    print("\nNezaplacené faktury:")
    for idx, inv in enumerate(unpaid):
        print(f"{idx + 1}. {inv['number']} | {inv['subject']['name']} | {inv['gross_price']} Kč | {inv['issued_on']}")

    sel = input("\nZadej čísla faktur k označení jako zaplacené (odděl čárkou): ")
    indices = [int(s.strip()) - 1 for s in sel.split(",") if s.strip()]
    paid_date = input("Datum úhrady (YYYY-MM-DD): ")

    for idx in indices:
        inv = unpaid[idx]
        mark_invoice_paid(inv["id"], paid_date)
        print(f"Faktura {inv['number']} označena jako zaplacená.")

    print("\nHotovo.")


if __name__ == "__main__":
    main()
