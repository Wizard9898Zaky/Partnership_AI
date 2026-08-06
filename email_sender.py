import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EmailSender:
    """
    A class to send emails using a Gmail account.

    SAFETY NOTE: this class is not currently wired into any part of
    Partnership_AI's action registry - nothing calls it yet. If it is
    ever connected as an autonomous action (an agent deciding on its
    own to send an email), send_email() below requires an explicit
    confirm=True on every call. This is a deliberate, cheap
    human-in-the-loop gate: an agent path that reaches this function
    without a human explicitly setting confirm=True will get a
    ConfirmationRequiredError instead of silently mailing someone.
    """

    def __init__(self, sender_email=None, sender_password=None, receiver_email=None):
        """
        Initialize the EmailSender class.

        Credentials default to environment variables
        (PARTNERSHIP_AI_SENDER_EMAIL / PARTNERSHIP_AI_SENDER_PASSWORD)
        rather than being passed as plain arguments - the previous
        __init__ signature encouraged callers to inline a raw password
        string (as its own example usage below did), which is the kind
        of thing that ends up hardcoded and committed to source control.
        Use a Gmail App Password here, not the account password itself.

        Args:
            sender_email (str): The email of the sender. Falls back to
                PARTNERSHIP_AI_SENDER_EMAIL if not given.
            sender_password (str): An app password for the sender
                account. Falls back to PARTNERSHIP_AI_SENDER_PASSWORD.
            receiver_email (str): The email of the receiver.
        """
        self.sender_email = sender_email or os.environ.get('PARTNERSHIP_AI_SENDER_EMAIL')
        self.sender_password = sender_password or os.environ.get('PARTNERSHIP_AI_SENDER_PASSWORD')
        self.receiver_email = receiver_email
        if not self.sender_email or not self.sender_password:
            logger.warning(
                'No sender credentials provided or found in environment '
                '(PARTNERSHIP_AI_SENDER_EMAIL / PARTNERSHIP_AI_SENDER_PASSWORD). '
                'send_email() will fail until these are set.'
            )

    def send_email(self, subject, message, confirm: bool = False):
        """
        Send an email to the receiver.

        Args:
            subject (str): The subject of the email.
            message (str): The content of the email.
            confirm (bool): Must be explicitly True to actually send.
                Defaults to False so that any code path - autonomous
                or otherwise - that calls this without a human
                deliberately opting in gets a loud error instead of a
                silently-sent email.
        """
        if not confirm:
            raise PermissionError(
                "send_email() requires confirm=True. This is a deliberate "
                "safeguard: nothing should send email on a user's behalf "
                "without an explicit, human-reviewed confirmation."
            )
        # FIX: validate credentials at send time, not just at init
        if not self.sender_email or not self.sender_password:
            raise ValueError(
                "SMTP credentials not configured. Set PARTNERSHIP_AI_SENDER_EMAIL "
                "and PARTNERSHIP_AI_SENDER_PASSWORD environment variables."
            )
        # FIX: validate recipient
        recipient = self.receiver_email
        if not recipient:
            raise ValueError("No recipient email address specified.")
        try:
            # Create a MIMEMultipart object
            msg = MIMEMultipart()
            msg['From'] = self.sender_email
            msg['To'] = recipient
            msg['Subject'] = subject

            # Attach the message to the MIMEMultipart object
            msg.attach(MIMEText(message, 'plain'))

            # FIX: add 10s timeout to prevent indefinite blocking
            server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
            server.starttls()
            server.login(self.sender_email, self.sender_password)
            text = msg.as_string()
            server.sendmail(self.sender_email, recipient, text)
            server.quit()

            logger.info('Email sent successfully')
        except Exception as e:
            logger.error('Failed to send email: %s', str(e))
            raise

# Example usage:
if __name__ == '__main__':
    # Set these as real environment variables rather than inlining them:
    #   export PARTNERSHIP_AI_SENDER_EMAIL='you@gmail.com'
    #   export PARTNERSHIP_AI_SENDER_PASSWORD='<gmail app password>'
    email_sender = EmailSender(receiver_email='receiver-email@example.com')
    email_sender.send_email('Test Email', 'This is a test email sent using Python.', confirm=True)
