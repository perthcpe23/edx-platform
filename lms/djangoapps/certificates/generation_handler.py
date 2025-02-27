"""
Course certificate generation handler.

These methods check to see if a certificate can be generated (created if it does not already exist, or updated if it
exists but its state can be altered). If so, a celery task is launched to do the generation. If the certificate
cannot be generated, a message is logged and no further action is taken.
"""

import logging

from edx_toggles.toggles import LegacyWaffleFlagNamespace

from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.certificates.models import (
    CertificateInvalidation,
    CertificateStatuses,
    CertificateWhitelist,
    GeneratedCertificate
)
from lms.djangoapps.certificates.queue import XQueueCertInterface
from lms.djangoapps.certificates.tasks import CERTIFICATE_DELAY_SECONDS, generate_certificate
from lms.djangoapps.certificates.utils import emit_certificate_event, has_html_certificates_enabled
from lms.djangoapps.instructor.access import list_with_level
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.certificates.api import auto_certificate_generation_enabled
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)

WAFFLE_FLAG_NAMESPACE = LegacyWaffleFlagNamespace(name='certificates_revamp')

# .. toggle_name: certificates_revamp.use_allowlist
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to enable the course certificates allowlist (aka V2 of the certificate whitelist)
#   on a per-course run basis.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2021-01-27
# .. toggle_target_removal_date: 2022-01-27
# .. toggle_tickets: MICROBA-918
CERTIFICATES_USE_ALLOWLIST = CourseWaffleFlag(
    waffle_namespace=WAFFLE_FLAG_NAMESPACE,
    flag_name='use_allowlist',
    module_name=__name__,
)


# .. toggle_name: certificates_revamp.use_updated
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to enable the updated regular (non-allowlist) course certificate logic on a
#   per-course run basis.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2021-03-05
# .. toggle_target_removal_date: 2022-03-05
# .. toggle_tickets: MICROBA-923
CERTIFICATES_USE_UPDATED = CourseWaffleFlag(
    waffle_namespace=WAFFLE_FLAG_NAMESPACE,
    flag_name='use_updated',
    module_name=__name__,
)


def can_generate_certificate_task(user, course_key):
    """
    Determine if we can create a task to generate a certificate for this user in this course run.

    This will return True if either:
    - the course run is using the allowlist and the user is on the allowlist, or
    - the course run is using v2 course certificates
    """
    if is_using_certificate_allowlist_and_is_on_allowlist(user, course_key):
        return True
    elif _is_using_v2_course_certificates(course_key):
        return True

    return False


def generate_certificate_task(user, course_key):
    """
    Create a task to generate a certificate for this user in this course run, if the user is eligible and a certificate
    can be generated.

    If the allowlist is enabled for this course run and the user is on the allowlist, the allowlist logic will be used.
    Otherwise, the regular course certificate generation logic will be used.
    """
    if is_using_certificate_allowlist_and_is_on_allowlist(user, course_key):
        log.info(f'{course_key} is using allowlist certificates, and the user {user.id} is on its allowlist. Attempt '
                 f'will be made to generate an allowlist certificate.')
        return generate_allowlist_certificate_task(user, course_key)

    elif _is_using_v2_course_certificates(course_key):
        log.info(f'{course_key} is using v2 course certificates. Attempt will be made to generate a certificate for '
                 f'user {user.id}.')
        return generate_regular_certificate_task(user, course_key)

    log.info(f'Neither an allowlist nor a v2 course certificate can be generated for {user.id} : {course_key}.')
    return False


def generate_allowlist_certificate_task(user, course_key):
    """
    Create a task to generate an allowlist certificate for this user in this course run.
    """
    if not _can_generate_allowlist_certificate(user, course_key):
        log.info(f'Cannot generate an allowlist certificate for {user.id} : {course_key}')
        return False

    log.info(f'About to create an allowlist certificate task for {user.id} : {course_key}')

    kwargs = {
        'student': str(user.id),
        'course_key': str(course_key),
        'allowlist_certificate': True
    }
    generate_certificate.apply_async(countdown=CERTIFICATE_DELAY_SECONDS, kwargs=kwargs)
    return True


def generate_regular_certificate_task(user, course_key):
    """
    Create a task to generate a regular (non-allowlist) certificate for this user in this course run, if the user is
    eligible and a certificate can be generated.
    """
    if not _can_generate_v2_certificate(user, course_key):
        log.info(f'Cannot generate a v2 course certificate for {user.id} : {course_key}')
        return False

    log.info(f'About to create a v2 course certificate task for {user.id} : {course_key}')

    kwargs = {
        'student': str(user.id),
        'course_key': str(course_key),
        'v2_certificate': True
    }
    generate_certificate.apply_async(countdown=CERTIFICATE_DELAY_SECONDS, kwargs=kwargs)
    return True


def _can_generate_allowlist_certificate(user, course_key):
    """
    Check if an allowlist certificate can be generated (created if it doesn't already exist, or updated if it does
    exist) for this user, in this course run.
    """
    if not is_using_certificate_allowlist(course_key):
        # This course run is not using the allowlist feature
        log.info(f'{course_key} is not using the certificate allowlist. Certificate cannot be generated.')
        return False

    if not auto_certificate_generation_enabled():
        # Automatic certificate generation is globally disabled
        log.info('Automatic certificate generation is globally disabled. Certificate cannot be generated.')
        return False

    if CertificateInvalidation.has_certificate_invalidation(user, course_key):
        # The invalidation list overrides the allowlist
        log.info(f'{user.id} : {course_key} is on the certificate invalidation list. Certificate cannot be generated.')
        return False

    enrollment_mode, __ = CourseEnrollment.enrollment_mode_for_user(user, course_key)
    if enrollment_mode is None:
        log.info(f'{user.id} : {course_key} does not have an enrollment. Certificate cannot be generated.')
        return False

    if not IDVerificationService.user_is_verified(user):
        log.info(f'{user.id} does not have a verified id. Certificate cannot be generated.')
        return False

    if not _is_on_certificate_allowlist(user, course_key):
        log.info(f'{user.id} : {course_key} is not on the certificate allowlist. Certificate cannot be generated.')
        return False

    log.info(f'{user.id} : {course_key} is on the certificate allowlist')
    cert = GeneratedCertificate.certificate_for_student(user, course_key)
    return _can_generate_allowlist_certificate_for_status(cert)


def _can_generate_v2_certificate(user, course_key):
    """
    Check if a v2 course certificate can be generated (created if it doesn't already exist, or updated if it does
    exist) for this user, in this course run.
    """
    if not _is_using_v2_course_certificates(course_key):
        # This course run is not using the v2 course certificate feature
        log.info(f'{course_key} is not using v2 course certificates. Certificate cannot be generated.')
        return False

    # TODO: Further implementation will be added in MICROBA-923
    log.warning(f'Ignoring check on V2 course certificates for {user.id}: {course_key}')
    return False


def is_using_certificate_allowlist_and_is_on_allowlist(user, course_key):
    """
    Return True if both:
    1) the course run is using the allowlist, and
    2) if the user is on the allowlist for this course run
    """
    return is_using_certificate_allowlist(course_key) and _is_on_certificate_allowlist(user, course_key)


def is_using_certificate_allowlist(course_key):
    """
    Check if the course run is using the allowlist, aka V2 of certificate whitelisting
    """
    return CERTIFICATES_USE_ALLOWLIST.is_enabled(course_key)


def _is_using_v2_course_certificates(course_key):
    """
    Return True if the course run is using v2 course certificates
    """
    return CERTIFICATES_USE_UPDATED.is_enabled(course_key)


def _is_on_certificate_allowlist(user, course_key):
    """
    Check if the user is on the allowlist, and is enabled for the allowlist, for this course run
    """
    return CertificateWhitelist.objects.filter(user=user, course_id=course_key, whitelist=True).exists()


def _can_generate_allowlist_certificate_for_status(cert):
    """
    Check if the user's certificate status allows certificate generation
    """
    if cert is None:
        return True

    if cert.status == CertificateStatuses.downloadable:
        log.info('Certificate with status {status} already exists for {user} : {course}, and is NOT eligible for '
                 'allowlist generation. Certificate cannot be generated.'
                 .format(status=cert.status, user=cert.user.id, course=cert.course_id))
        return False

    log.info('Certificate with status {status} already exists for {user} : {course}, and is eligible for allowlist '
             'generation'
             .format(status=cert.status, user=cert.user.id, course=cert.course_id))
    return True


def generate_user_certificates(student, course_key, course=None, insecure=False, generation_mode='batch',
                               forced_grade=None):
    """
    It will add the add-cert request into the xqueue.

    A new record will be created to track the certificate
    generation task.  If an error occurs while adding the certificate
    to the queue, the task will have status 'error'. It also emits
    `edx.certificate.created` event for analytics.

    This method has not yet been updated (it predates the certificates revamp). If modifying this method,
    see also generate_user_certificates() in generation.py (which is very similar but is called from a celery task).
    In the future these methods will be unified.

    Args:
        student (User)
        course_key (CourseKey)

    Keyword Arguments:
        course (Course): Optionally provide the course object; if not provided
            it will be loaded.
        insecure - (Boolean)
        generation_mode - who has requested certificate generation. Its value should `batch`
        in case of django command and `self` if student initiated the request.
        forced_grade - a string indicating to replace grade parameter. if present grading
                       will be skipped.
    """
    if is_using_certificate_allowlist_and_is_on_allowlist(student, course_key):
        # Note that this will launch an asynchronous task, and so cannot return the certificate status. This is a
        # change from the older certificate code that tries to immediately create a cert.
        log.info(f'{course_key} is using allowlist certificates, and the user {student.id} is on its allowlist. '
                 f'Attempt will be made to regenerate an allowlist certificate.')
        return generate_allowlist_certificate_task(student, course_key)

    if not course:
        course = modulestore().get_course(course_key, depth=0)

    beta_testers_queryset = list_with_level(course, 'beta')

    if beta_testers_queryset.filter(username=student.username):
        message = 'Cancelling course certificate generation for user [{}] against course [{}], user is a Beta Tester.'
        log.info(message.format(student.username, course_key))
        return

    xqueue = XQueueCertInterface()
    if insecure:
        xqueue.use_https = False

    generate_pdf = not has_html_certificates_enabled(course)

    cert = xqueue.add_cert(
        student,
        course_key,
        course=course,
        generate_pdf=generate_pdf,
        forced_grade=forced_grade
    )

    message = 'Queued Certificate Generation task for {user} : {course}'
    log.info(message.format(user=student.id, course=course_key))

    # If cert_status is not present in certificate valid_statuses (for example unverified) then
    # add_cert returns None and raises AttributeError while accessing cert attributes.
    if cert is None:
        return

    if CertificateStatuses.is_passing_status(cert.status):
        emit_certificate_event('created', student, course_key, course, {
            'user_id': student.id,
            'course_id': str(course_key),
            'certificate_id': cert.verify_uuid,
            'enrollment_mode': cert.mode,
            'generation_mode': generation_mode
        })
    return cert.status


def regenerate_user_certificates(student, course_key, course=None,
                                 forced_grade=None, template_file=None, insecure=False):
    """
    Add the regen-cert request into the xqueue.

    A new record will be created to track the certificate
    generation task.  If an error occurs while adding the certificate
    to the queue, the task will have status 'error'.

    This method has not yet been updated (it predates the certificates revamp).

    Args:
        student (User)
        course_key (CourseKey)

    Keyword Arguments:
        course (Course): Optionally provide the course object; if not provided
            it will be loaded.
        grade_value - The grade string, such as "Distinction"
        template_file - The template file used to render this certificate
        insecure - (Boolean)
    """
    if is_using_certificate_allowlist_and_is_on_allowlist(student, course_key):
        log.info(f'{course_key} is using allowlist certificates, and the user {student.id} is on its allowlist. '
                 f'Attempt will be made to regenerate an allowlist certificate.')
        return generate_allowlist_certificate_task(student, course_key)

    xqueue = XQueueCertInterface()
    if insecure:
        xqueue.use_https = False

    if not course:
        course = modulestore().get_course(course_key, depth=0)

    generate_pdf = not has_html_certificates_enabled(course)
    log.info(
        "Started regenerating certificates for user %s in course %s with generate_pdf status: %s",
        student.username, str(course_key), generate_pdf
    )

    xqueue.regen_cert(
        student,
        course_key,
        course=course,
        forced_grade=forced_grade,
        template_file=template_file,
        generate_pdf=generate_pdf
    )
    return True
