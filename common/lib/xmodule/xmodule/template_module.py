"""
Template module
"""

from string import Template

from lxml import etree
from pkg_resources import resource_string
from web_fragments.fragment import Fragment
from xmodule.editing_module import EditingMixin
from xmodule.raw_module import RawMixin
from xmodule.util.xmodule_django import add_webpack_to_fragment
from xmodule.x_module import (
    HTMLSnippet,
    ResourceTemplates,
    shim_xmodule_js,
    XModuleMixin,
    XModuleDescriptorToXBlockMixin,
    XModuleToXBlockMixin,
)
from xmodule.xml_module import XmlMixin


class CustomTagBlock(
    RawMixin,
    XmlMixin,
    EditingMixin,
    XModuleDescriptorToXBlockMixin,
    XModuleToXBlockMixin,
    HTMLSnippet,
    ResourceTemplates,
    XModuleMixin,
):  # pylint: disable=abstract-method
    """
    This module supports tags of the form
    <customtag option="val" option2="val2" impl="tagname"/>

    In this case, $tagname should refer to a file in data/custom_tags, which
    contains a Python string.Template formatted template that uses ${option} and
    ${option2} for the content.

    For instance:

    data/mycourse/custom_tags/book::
        More information given in <a href="/book/${page}">the text</a>

    course.xml::
        ...
        <customtag page="234" impl="book"/>
        ...

    Renders to::
        More information given in <a href="/book/234">the text</a>
    """
    resources_dir = None
    template_dir_name = 'customtag'

    preview_view_js = {
        'js': [],
        'xmodule_js': resource_string(__name__, 'js/src/xmodule.js'),
    }
    preview_view_css = {
        'scss': [],
    }
    studio_view_js = {
        'js': [resource_string(__name__, 'js/src/raw/edit/xml.js')],
        'xmodule_js': resource_string(__name__, 'js/src/xmodule.js'),
    }
    studio_view_css = {
        'scss': [resource_string(__name__, 'css/codemirror/codemirror.scss')],
    }

    def studio_view(self, _context):
        """
        Return the studio view.
        """
        fragment = Fragment(
            self.system.render_template(self.mako_template, self.get_context())
        )
        add_webpack_to_fragment(fragment, 'CustomTagBlockStudio')
        shim_xmodule_js(fragment, 'XMLEditingDescriptor')
        return fragment

    def render_template(self, system, xml_data):
        '''Render the template, given the definition xml_data'''
        xmltree = etree.fromstring(xml_data)
        if 'impl' in xmltree.attrib:
            template_name = xmltree.attrib['impl']
        else:
            # VS[compat]  backwards compatibility with old nested customtag structure
            child_impl = xmltree.find('impl')
            if child_impl is not None:
                template_name = child_impl.text
            else:
                # TODO (vshnayder): better exception type
                raise Exception("Could not find impl attribute in customtag {0}"
                                .format(self.location))

        params = dict(list(xmltree.items()))

        # cdodge: look up the template as a module
        template_loc = self.location.replace(category='custom_tag_template', name=template_name)

        template_module = system.load_item(template_loc)
        template_module_data = template_module.data
        template = Template(template_module_data)
        return template.safe_substitute(params)

    @property
    def rendered_html(self):
        return self.render_template(self.system, self.data)

    def student_view(self, _context):
        """
        Renders the student view.
        """
        fragment = Fragment()
        fragment.add_content(self.rendered_html)
        return fragment

    def export_to_file(self):
        """
        Custom tags are special: since they're already pointers, we don't want
        to export them in a file with yet another layer of indirection.
        """
        return False
