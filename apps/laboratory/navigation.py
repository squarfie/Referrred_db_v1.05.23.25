"""Keep filter context on known same-site laboratory pages."""
from urllib.parse import urlencode, urlsplit
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def return_url(request):
    value = request.POST.get('next') or request.GET.get('next') or ''
    if (value.startswith('/') and not value.startswith('//')
            and urlsplit(value).path.startswith(reverse('laboratory_biobank_list').rsplit('/', 2)[0] + '/')
            and url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()},
                                                require_https=request.is_secure())):
        return value
    return ''


def detail_url(request, isolate_id, *, main_lab=False):
    url = reverse('main_lab_isolate_detail' if main_lab else 'laboratory_isolate_detail', args=[isolate_id])
    origin = return_url(request)
    return url + ('?' + urlencode({'next': origin}) if origin else '')
