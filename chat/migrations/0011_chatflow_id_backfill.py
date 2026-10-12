from django.db import migrations
import random


def generate_chatflow_id():
    part1 = str(random.randint(1000, 9999))
    part2 = str(random.randint(1000, 9999))
    return f"{part1}-{part2}"


def backfill_chatflow_ids(apps, schema_editor):
    UserProfile = apps.get_model('chat', 'UserProfile')
    existing_ids = set(
        UserProfile.objects.exclude(chatflow_id=None)
        .exclude(chatflow_id='')
        .values_list('chatflow_id', flat=True)
    )
    for profile in UserProfile.objects.filter(chatflow_id__isnull=True):
        while True:
            new_id = generate_chatflow_id()
            if new_id not in existing_ids:
                existing_ids.add(new_id)
                profile.chatflow_id = new_id
                profile.save(update_fields=['chatflow_id'])
                break
    for profile in UserProfile.objects.filter(chatflow_id=''):
        while True:
            new_id = generate_chatflow_id()
            if new_id not in existing_ids:
                existing_ids.add(new_id)
                profile.chatflow_id = new_id
                profile.save(update_fields=['chatflow_id'])
                break


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0010_userprofile_chatflow_id_chatcontact'),
    ]

    operations = [
        migrations.RunPython(backfill_chatflow_ids, migrations.RunPython.noop),
    ]