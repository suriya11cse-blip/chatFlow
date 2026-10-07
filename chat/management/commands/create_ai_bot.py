from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from chat.models import UserProfile


class Command(BaseCommand):
    help = 'Create or update the ChatFlow AI bot user'

    def handle(self, *args, **kwargs):
        username = 'ChatFlow AI'
        user, created = User.objects.get_or_create(username=username)

        if created:
            user.set_unusable_password()
            user.is_active = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f'✓ Created user: {username}'))
        else:
            self.stdout.write(self.style.WARNING(f'! User already exists: {username}'))

        profile, p_created = UserProfile.objects.get_or_create(user=user)
        profile.is_ai_bot = True
        profile.bio = 'Your friendly AI assistant — ask me anything!'
        profile.handle = 'chatflowai'
        if not profile.chatflow_id:
            profile.chatflow_id = '0000-0001'
        profile.save()

        self.stdout.write(self.style.SUCCESS('✓ AI bot profile ready'))
        self.stdout.write(self.style.SUCCESS(f'  Username : {username}'))
        self.stdout.write(self.style.SUCCESS(f'  Handle   : @chatflowai'))
        self.stdout.write(self.style.SUCCESS(f'  ChatFlow ID: {profile.chatflow_id}'))