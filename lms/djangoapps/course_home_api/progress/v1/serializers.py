"""
Progress Tab Serializers
"""
from rest_framework import serializers
from rest_framework.reverse import reverse


class CourseGradeSerializer(serializers.Serializer):
    percent = serializers.FloatField()
    is_passing = serializers.SerializerMethodField()

    def get_is_passing(self, grade):
        return grade.passed


class GradingPolicySerializer(serializers.Serializer):
    assignment_policies = serializers.SerializerMethodField()
    grade_range = serializers.SerializerMethodField()

    def get_assignment_policies(self, grading_policy):
        return [{
            'type': assignment_policy['type'],
            'weight': assignment_policy['weight'],
        } for assignment_policy in grading_policy['GRADER']]

    def get_grade_range(self, grading_policy):
        return grading_policy['GRADE_CUTOFFS']


class SubsectionSerializer(serializers.Serializer):
    assignment_type = serializers.SerializerMethodField()
    display_name = serializers.CharField()
    total_points = serializers.SerializerMethodField()
    is_graded_assignment = serializers.SerializerMethodField()
    percent_graded = serializers.FloatField()
    show_correctness = serializers.CharField()
    show_grades = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()

    def get_assignment_type(self, subsection):
        return subsection.format

    def get_total_points(self, subsection):
        return {
            'num_earned': subsection.graded_total.earned,
            'num_possible': subsection.graded_total.possible
        }

    def get_is_graded_assignment(self, subsection):
        return subsection.graded

    def get_url(self, subsection):
        relative_path = reverse('jump_to', args=[self.context['course_key'], subsection.location])
        request = self.context['request']
        return request.build_absolute_uri(relative_path)

    def get_show_grades(self, subsection):
        return subsection.show_grades(self.context['staff_access'])


class ChapterSerializer(serializers.Serializer):
    """
    Serializer for chapters in courseware_summary
    """
    display_name = serializers.CharField()
    subsections = SubsectionSerializer(source='sections', many=True)


class CertificateDataSerializer(serializers.Serializer):
    cert_status = serializers.CharField()
    cert_web_view_url = serializers.CharField()
    download_url = serializers.CharField()


class VerificationDataSerializer(serializers.Serializer):
    """
    Serializer for verification data object
    """
    link = serializers.URLField()
    status = serializers.CharField()
    status_date = serializers.DateTimeField()


class ProgressTabSerializer(serializers.Serializer):
    """
    Serializer for progress tab
    """
    certificate_data = CertificateDataSerializer()
    completion_summary = serializers.DictField()
    course_grade = CourseGradeSerializer()
    courseware_point_summary = ChapterSerializer(many=True)
    enrollment_mode = serializers.CharField()
    grading_policy = GradingPolicySerializer()
    studio_url = serializers.CharField()
    verification_data = VerificationDataSerializer()
