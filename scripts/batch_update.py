import json
import argparse

from Acquisition import aq_parent
import transaction
from webdav.Lockable import ResourceLockedError

from Products.Archetypes.exceptions import ReferenceException
from Products.CMFCore.utils import getToolByName
from plone.app.iterate.interfaces import ICheckinCheckoutPolicy

from pleiades.dump import getSite, spoofRequest


FIELD_NAMES = {'attested': 'nameAttested',
               'language': 'nameLanguage',
               'nameType': 'nameType',
               'transcriptionAccuracy': 'accuracy',
               'transcriptionCompleteness': 'completeness',
               'associationCertainty': 'associationCertainty',
               'details': 'text',
               'featureType': 'featureType',
               'associationCertainty': 'associationCertainty',
               'details': 'text',
               'archaeologicalRemains': 'archaeologicalRemains',
               'locationType': 'locationType',
               }

CONTENT_TYPES = ['Connection',
                 'Feature',
                 'Location',
                 'Name',
                 'Place',
                 ]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Update Pleiades content.')
    parser.add_argument('--dry-run', action='store_true', default=False,
                        dest='dry_run', help='No changes will be made.')
    parser.add_argument('--create', action='store_true', default=False,
                        dest='create', help='Process content additions.')
    parser.add_argument('--workflow', choices=['publish', 'review', 'draft'],
                        default='publish',
                        help='Direct edit, or set as review or draft.')
    parser.add_argument('--message', default="Editorial adjustment (batch)",
                        help='Commit message.')
    parser.add_argument('--owner', help='Content owner. Defaults to "admin"')
    parser.add_argument('--creators', nargs='*', default=[],
                        help='Creators. Separated by spaces.')
    parser.add_argument('--contributors',  default=[],
                        nargs='*', help='Contributors. Separated by spaces.')
    parser.add_argument('file', type=file, help='Path to JSON import file')
    parser.add_argument('-c', help='Optional Zope configuration file.')

    try:
        args = parser.parse_args()
    except IOError, msg:
        parser.error(str(msg))

    updates = json.loads(args.file.read()).get('updates', [])

    app = spoofRequest(app)
    site = getSite(app)

    workflow = getToolByName(site, "portal_workflow")
    membership = getToolByName(site, "portal_membership")

    print
    print "Starting batch content update..."
    print

    if args.workflow not in ['review', 'draft']:
        print "Direct content update. Change message will be ignored."
        print

    for update in updates:
        content_type = None
        creating = False
        new_id = ''
        path, values = update.items()[0]
        if '::' in path:
            content_type, path = path.split('::')
            if not args.create:
                continue
            creating = True
        if path.startswith('/'):
            path = path[1:]
        if creating:
            print "Creating {} at path: {}".format(content_type, path)
            path, new_id = path.rsplit('/', 1)
        else:
            print "Updating object at path: {}".format(path)
        try:
            content = site.restrictedTraverse(path.encode('utf-8'))
        except (KeyError, AttributeError):
            print "Not found. Skipping."
            print
            continue
        if creating:
            content.invokeFactory(content_type, new_id)
            content = content[new_id]
            print 'Created {} with id "{}"'.format(content.portal_type, new_id)
        else:
            print 'Found {} with title "{}"'.format(content.portal_type,
                                                    content.Title())
        status = workflow.getStatusOf("pleiades_entity_workflow", content)
        review_state = status and status.get('review_state',
                                             'unknown') or 'unknown'
        print "Workflow state: {}.".format(review_state)

        container = aq_parent(content)
        if args.workflow in ['review', 'draft'] and not creating:
            policy = ICheckinCheckoutPolicy(content)
            working_copy = policy.checkout(container)
            print "Checked out working copy."
        else:
            policy = None
            working_copy = None

        change_note = args.message
        for key, modify in values.items():
            if key == 'change_note':
                change_note = modify
                continue
            if key == 'id' and creating:
                print "Content id change ignored during creation."
                continue
            if key == 'id':
                if modify['mode'] == 'replace':
                    old_id = content.getId()
                    new_id = modify['values'][0].encode('utf-8')
                    try:
                        container.manage_renameObjects([old_id], [new_id])
                        content = container[new_id]
                        print 'Renamed "{}" to "{}".'.format(old_id, new_id)
                    except ResourceLockedError:
                        print "Locked. Cannot change id if checked out."
                else:
                    print "Content Id cannot be deleted or appended. Skipping."
                continue
            if key in FIELD_NAMES:
                key = FIELD_NAMES[key]
            field = content.getField(key)
            if field is not None:
                old_value = field.getRaw(content)
                if modify['mode'] == 'delete':
                    value = None
                elif modify['mode'] == 'replace':
                    value = modify['values']
                    if not isinstance(old_value, list):
                        value = value[0]
                elif modify['mode'] == 'append':
                    if (not isinstance(old_value, list) and
                        not isinstance(old_value, tuple)):
                        print '"{}" is not a list. Cannot append.'.format(key)
                        continue
                    value = list(old_value)
                    value.extend(modify['values'])
                if key == 'description':
                    content.setDescription(value)
                elif key == 'title':
                    content.setTitle(value)
                elif key == 'subject':
                    content.setSubject(value)
                else:
                    try:
                        field.set(content, value)
                    except  ReferenceException:
                        print 'Invalid reference on field "{}". Skipping.'.format(key)
                        continue
                if isinstance(value, basestring):
                    value = value.encode('utf-8')
                print 'Set "{}" to: "{}". Old value: "{}"'.format(key,
                                                                  value,
                                                                  old_value)
            else:
                print 'Field "{}" does not exist. Skipping.'.format(key)

        if args.creators:
            content.setCreators(args.creators)
            print "Set creators to {}".format(args.creators)
        if args.contributors:
            content.setContributors(args.contributors)
            print "Set contributors to {}".format(args.contributors)
        if args.owner:
            member = membership.getMemberById(args.owner)
            user = member.getUser()
            content.changeOwnership(user, recursive=False)
            content.reindexObjectSecurity()
            print "Set owner to {}".format(args.owner)

        if args.workflow in ['review', 'draft'] and not creating:
            if args.workflow == 'review' and review_state == 'drafting':
                workflow.doActionFor(content, 'submit')
                print "Set workflow state to review."
            policy = ICheckinCheckoutPolicy(working_copy)
            policy.checkin(change_note)
            print "Checked in working copy."

        if creating and args.workflow in ['review', 'publish']:
            workflow.doActionFor(content, 'submit')
            print "Set workflow state to reviewing."
        if creating and args.workflow == 'publish':
            workflow.doActionFor(content, 'publish')
            print "Set workflow state to published."
        print 'Updated "{}".'.format(content.Title())
        print

    if args.dry_run:
        print "Dry run. No changes made in Plone."
    else:
        print "Updated content in Plone."
        transaction.commit()