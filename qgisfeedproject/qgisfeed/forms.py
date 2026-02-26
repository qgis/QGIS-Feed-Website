from django.contrib.auth.models import User
from django.contrib.gis import forms
from django.forms import ValidationError
from django.utils import timezone

from .languages import LANGUAGES
from .models import CharacterLimitConfiguration, QgisFeedEntry


class HomePageFilterForm(forms.Form):
    """
    Form for feed entry filter on the home page
    """

    empty_lang = ("", "Select a language")
    LANG_CHOICES = (empty_lang,) + LANGUAGES

    lang = forms.ChoiceField(
        choices=LANG_CHOICES, required=False, widget=forms.Select()
    )

    publish_from = forms.CharField(
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "input",
            }
        ),
    )


class FeedEntryFilterForm(forms.Form):
    """
    Form for feed entry filter on the feed list
    """

    empty_lang = ("", "Select a language")
    LANG_CHOICES = (empty_lang,) + LANGUAGES
    title = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "Title"}),
    )
    author = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "Author"}),
    )

    language_filter = forms.ChoiceField(
        choices=LANG_CHOICES, required=False, widget=forms.Select()
    )

    need_review = forms.ChoiceField(
        required=False,
        widget=forms.Select(),
        choices=[("", "Select an option"), (1, "Yes"), (0, "No")],
    )

    publish_from = forms.CharField(
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "input",
            }
        ),
    )
    publish_to = forms.CharField(
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "input",
            }
        ),
    )


class MapWidget(forms.OSMWidget):

    def __init__(self, attrs=None):
        default_attrs = {"default_lat": 0, "default_lon": 0, "default_zoom": 2}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)

    class Media:
        js = ["ol/ol-7.2.2.js"]
        css = {"all": ["ol/ol-7.2.2.css"]}


class FeedItemForm(forms.ModelForm):
    """
    Form for feed entry add or update
    """

    sticky = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "checkbox"}),
        help_text="Do not mark the entry as sticky unless it is urgent.",
    )

    reviewers = forms.MultipleChoiceField(
        required=False,
        widget=forms.SelectMultiple(),
        label="Reviewers",
        help_text="Select reviewers for this entry",
    )

    class Meta:
        model = QgisFeedEntry
        fields = [
            "title",
            "image",
            "content",
            "url",
            "sticky",
            "sorting",
            "language_filter",
            "spatial_filter",
            "publish_from",
            "publish_to",
        ]

    def __init__(self, *args, **kwargs):
        # Extract the user from kwargs before calling parent's __init__
        self.user = kwargs.pop("user", None)
        super(FeedItemForm, self).__init__(*args, **kwargs)

        # Remove sticky field if user doesn't have the required permission
        if (
            self.user
            and not self.user.is_superuser
            and not self.user.has_perm("qgisfeed.publish_qgisfeedentry")
        ):
            del self.fields["sticky"]

        # Setup reviewers field
        self.fields["reviewers"].choices = self.get_reviewers_choices()

        # Pre-populate reviewers field if editing existing entry
        if self.instance and self.instance.pk:
            self.fields["reviewers"].initial = [
                r.pk for r in self.instance.reviewers.all()
            ]
        # Custom fields widget
        self.fields["title"].widget = forms.TextInput(
            attrs={
                "class": "input",
                "placeholder": "Title",
                "maxlength": self.fields["title"].max_length,
            }
        )
        self.fields["image"].widget = forms.FileInput(attrs={"class": "file-input"})
        self.fields["content"].widget = forms.Textarea(
            attrs={"class": "textarea", "placeholder": "Content", "rows": 5}
        )
        self.fields["url"].widget = forms.TextInput(
            attrs={"class": "input", "placeholder": "URL for more information link"}
        )
        self.fields["sorting"].widget = forms.NumberInput(
            attrs={
                "class": "input",
                "placeholder": "Increase to show at top of the list",
            }
        )
        self.fields["spatial_filter"].widget = MapWidget(
            attrs={
                "geom_type": "Polygon",
                "default_lat": 0,
                "default_lon": 0,
                "default_zoom": 2,
            }
        )
        self.fields["publish_from"].widget = forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={
                "type": "datetime-local",
                "class": "input",
            },
        )
        self.fields["publish_from"].initial = timezone.now()

        self.fields["publish_to"].widget = forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={
                "type": "datetime-local",
                "class": "input",
            },
        )
        self.fields["publish_to"].initial = timezone.now() + timezone.timedelta(days=10)

    def clean_content(self):
        content = self.cleaned_data["content"]
        try:
            config = CharacterLimitConfiguration.objects.get(field_name="content")
            content_max_length = config.max_characters
        except CharacterLimitConfiguration.DoesNotExist:
            content_max_length = 500

        if len(content) > content_max_length:
            raise ValidationError(
                f"Ensure this value has at most {str(content_max_length)} characters (it has {str(len(content))})."
            )
        return content

    def get_reviewers_choices(self):
        """Get list of users who can review (have publish permission)"""
        return (
            (u.pk, u.username)
            for u in User.objects.filter(is_active=True, email__isnull=False).exclude(
                email=""
            )
            if u.has_perm("qgisfeed.publish_qgisfeedentry")
        )


class FeedSocialSyndicationForm(forms.Form):
    """
    Form for feed syndication add or update
    """

    post_content = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"class": "textarea", "placeholder": "Content", "rows": 5}
        ),
    )
    # post_image = forms.FileInput(
    #     attrs={'class': 'file-input'}
    # )
