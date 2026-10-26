from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [('core', '0001_initial')]
    operations = [migrations.AlterModelOptions(name='course', options={'ordering': ['code']})]
