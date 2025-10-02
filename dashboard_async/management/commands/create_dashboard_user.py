"""
Management command to create dashboard users with temporary passwords.

Usage:
    python manage.py create_dashboard_user <username> [--password PASSWORD] [--email EMAIL] [--is-staff] [--is-superuser]
    
Examples:
    # Create a regular user with auto-generated password
    python manage.py create_dashboard_user john_doe --email john@example.com
    
    # Create a user with specific password
    python manage.py create_dashboard_user jane_doe --password TempPass123! --email jane@example.com
    
    # Create a staff user (can access Django admin)
    python manage.py create_dashboard_user admin_user --is-staff --email admin@example.com
    
    # Create a superuser (full admin privileges)
    python manage.py create_dashboard_user super_admin --is-superuser --email super@example.com
"""

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
import secrets
import string


class Command(BaseCommand):
    help = 'Create a dashboard user with a temporary password'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='Username for the new user')
        parser.add_argument(
            '--password',
            type=str,
            help='Password for the user (if not provided, will be auto-generated)',
            default=None
        )
        parser.add_argument(
            '--email',
            type=str,
            help='Email address for the user',
            default=''
        )
        parser.add_argument(
            '--is-staff',
            action='store_true',
            help='Give the user staff status (can access Django admin)',
            default=False
        )
        parser.add_argument(
            '--is-superuser',
            action='store_true',
            help='Give the user superuser status (all permissions)',
            default=False
        )

    def generate_password(self, length=16):
        """Generate a secure random password."""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        password = ''.join(secrets.choice(alphabet) for i in range(length))
        return password

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']
        email = options['email']
        is_staff = options['is_staff']
        is_superuser = options['is_superuser']

        # Check if user already exists
        if User.objects.filter(username=username).exists():
            raise CommandError(f'User "{username}" already exists')

        # Generate password if not provided
        password_was_generated = False
        if not password:
            password = self.generate_password()
            password_was_generated = True

        # Validate password
        try:
            validate_password(password)
        except ValidationError as e:
            self.stdout.write(self.style.ERROR('Password validation failed:'))
            for error in e.messages:
                self.stdout.write(self.style.ERROR(f'  - {error}'))
            raise CommandError('Invalid password. Please provide a stronger password.')

        # Create the user
        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_staff=is_staff or is_superuser,  # Superusers need staff status too
                is_superuser=is_superuser
            )
            
            self.stdout.write(self.style.SUCCESS(f'\n✓ Successfully created user "{username}"'))
            self.stdout.write('')
            self.stdout.write('User Details:')
            self.stdout.write(f'  Username: {username}')
            self.stdout.write(f'  Email: {email if email else "(not set)"}')
            self.stdout.write(f'  Staff Status: {"Yes" if user.is_staff else "No"}')
            self.stdout.write(f'  Superuser Status: {"Yes" if user.is_superuser else "No"}')
            self.stdout.write('')
            
            if password_was_generated:
                self.stdout.write(self.style.WARNING('⚠ Auto-generated password (save this securely):'))
                self.stdout.write(self.style.SUCCESS(f'  {password}'))
                self.stdout.write('')
                self.stdout.write(self.style.NOTICE('The user should change this password after first login.'))
            else:
                self.stdout.write(self.style.NOTICE('User can change their password at: /dashboard/password-change/'))
            
            self.stdout.write('')
            self.stdout.write(f'Login URL: /dashboard/login/')
            
        except Exception as e:
            raise CommandError(f'Error creating user: {str(e)}')

