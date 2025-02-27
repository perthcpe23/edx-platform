Course Certificate Allowlist Requirements
=========================================

Status
------
Accepted

Background
----------
Users can earn a course certificate in a particular course run (the certificate
is stored in the GeneratedCertificate model). If a user has not earned a certificate
but the course staff would like them to have a certificate anyway, the user can
be added to the certificate allowlist for the course run. The allowlist is currently
stored in the CertificateWhitelist model, and was previously referred to as the
certificate whitelist.

Requirements
------------
Even if a user is on the allowlist for a given course run, they won't necessarily
receive a course certificate in the *downloadable* state. In other words, the user
won't necessarily have a course certificate available to them. To receive a
downloadable allowlist course certificate, the following things must be true at
the time the certificate is generated:

* The user must be enrolled in the course
* The user must have an approved, unexpired, ID verification
* The user must be on the allowlist for the course run (see the CertificateWhitelist model)
* The user must not have an invalidated certificate for the course run (see the CertificateInvalidation model)
* Certificate generation must be enabled for the course run
* Automatic certificate generation must be enabled

Note: the above requirements were written for the allowlist, which assumes the
CourseWaffleFlag *certificates_revamp.use_allowlist* has been enabled for the
course run. If it has not been enabled, the prior logic will apply.
