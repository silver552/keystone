# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 OpenStack LLC
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from keystone import clean
from keystone.common import kvs
from keystone.common import utils
from keystone import exception
from keystone import identity


class Identity(kvs.Base, identity.Driver):
    # Public interface
    def authenticate(self, user_id=None, tenant_id=None, password=None):
        """Authenticate based on a user, tenant and password.

        Expects the user object to have a password field and the tenant to be
        in the list of tenants on the user.

        """
        user_ref = None
        tenant_ref = None
        metadata_ref = {}

        try:
            user_ref = self._get_user(user_id)
        except exception.UserNotFound:
            raise AssertionError('Invalid user / password')

        if not utils.check_password(password, user_ref.get('password')):
            raise AssertionError('Invalid user / password')

        if tenant_id is not None:
            if tenant_id not in self.get_projects_for_user(user_id):
                raise AssertionError('Invalid tenant')

            try:
                tenant_ref = self.get_project(tenant_id)
                metadata_ref = self.get_metadata(user_id, tenant_id)
            except exception.ProjectNotFound:
                tenant_ref = None
                metadata_ref = {}
            except exception.MetadataNotFound:
                metadata_ref = {}

        return (identity.filter_user(user_ref), tenant_ref, metadata_ref)

    def get_project(self, tenant_id):
        try:
            return self.db.get('tenant-%s' % tenant_id)
        except exception.NotFound:
            raise exception.ProjectNotFound(project_id=tenant_id)

    def get_projects(self):
        tenant_keys = filter(lambda x: x.startswith("tenant-"), self.db.keys())
        return [self.db.get(key) for key in tenant_keys]

    def get_project_by_name(self, tenant_name):
        try:
            return self.db.get('tenant_name-%s' % tenant_name)
        except exception.NotFound:
            raise exception.ProjectNotFound(project_id=tenant_name)

    def get_project_users(self, tenant_id):
        self.get_project(tenant_id)
        user_keys = filter(lambda x: x.startswith("user-"), self.db.keys())
        user_refs = [self.db.get(key) for key in user_keys]
        return filter(lambda x: tenant_id in x['tenants'], user_refs)

    def _get_user(self, user_id):
        try:
            return self.db.get('user-%s' % user_id)
        except exception.NotFound:
            raise exception.UserNotFound(user_id=user_id)

    def _get_user_by_name(self, user_name):
        try:
            return self.db.get('user_name-%s' % user_name)
        except exception.NotFound:
            raise exception.UserNotFound(user_id=user_name)

    def get_user(self, user_id):
        return identity.filter_user(self._get_user(user_id))

    def get_user_by_name(self, user_name):
        return identity.filter_user(self._get_user_by_name(user_name))

    def get_metadata(self, user_id=None, tenant_id=None,
                     domain_id=None, group_id=None):
        try:
            if user_id:
                return self.db.get('metadata-%s-%s' % (tenant_id, user_id))
            else:
                return self.db.get('metadata-%s-%s' % (tenant_id, group_id))
        except exception.NotFound:
            raise exception.MetadataNotFound()

    def get_role(self, role_id):
        try:
            return self.db.get('role-%s' % role_id)
        except exception.NotFound:
            raise exception.RoleNotFound(role_id=role_id)

    def list_users(self):
        user_ids = self.db.get('user_list', [])
        return [self.get_user(x) for x in user_ids]

    def list_roles(self):
        role_ids = self.db.get('role_list', [])
        return [self.get_role(x) for x in role_ids]

    # These should probably be part of the high-level API
    def add_user_to_project(self, tenant_id, user_id):
        self.get_project(tenant_id)
        user_ref = self._get_user(user_id)
        tenants = set(user_ref.get('tenants', []))
        tenants.add(tenant_id)
        self.update_user(user_id, {'tenants': list(tenants)})

    def remove_user_from_project(self, tenant_id, user_id):
        self.get_project(tenant_id)
        user_ref = self._get_user(user_id)
        tenants = set(user_ref.get('tenants', []))
        try:
            tenants.remove(tenant_id)
        except KeyError:
            raise exception.NotFound('User not found in tenant')
        self.update_user(user_id, {'tenants': list(tenants)})

    def get_projects_for_user(self, user_id):
        user_ref = self._get_user(user_id)
        return user_ref.get('tenants', [])

    def get_roles_for_user_and_project(self, user_id, tenant_id):
        self.get_user(user_id)
        self.get_project(tenant_id)
        try:
            metadata_ref = self.get_metadata(user_id, tenant_id)
        except exception.MetadataNotFound:
            metadata_ref = {}
        return metadata_ref.get('roles', [])

    def add_role_to_user_and_project(self, user_id, tenant_id, role_id):
        self.get_user(user_id)
        self.get_project(tenant_id)
        self.get_role(role_id)
        try:
            metadata_ref = self.get_metadata(user_id, tenant_id)
        except exception.MetadataNotFound:
            metadata_ref = {}
        roles = set(metadata_ref.get('roles', []))
        if role_id in roles:
            msg = ('User %s already has role %s in tenant %s'
                   % (user_id, role_id, tenant_id))
            raise exception.Conflict(type='role grant', details=msg)
        roles.add(role_id)
        metadata_ref['roles'] = list(roles)
        self.update_metadata(user_id, tenant_id, metadata_ref)

    def remove_role_from_user_and_project(self, user_id, tenant_id, role_id):
        try:
            metadata_ref = self.get_metadata(user_id, tenant_id)
        except exception.MetadataNotFound:
            metadata_ref = {}
        roles = set(metadata_ref.get('roles', []))
        if role_id not in roles:
            msg = 'Cannot remove role that has not been granted, %s' % role_id
            raise exception.RoleNotFound(message=msg)

        roles.remove(role_id)
        metadata_ref['roles'] = list(roles)
        self.update_metadata(user_id, tenant_id, metadata_ref)

    # CRUD
    def create_user(self, user_id, user):
        user['name'] = clean.user_name(user['name'])
        try:
            self.get_user(user_id)
        except exception.UserNotFound:
            pass
        else:
            msg = 'Duplicate ID, %s.' % user_id
            raise exception.Conflict(type='user', details=msg)

        try:
            self.get_user_by_name(user['name'])
        except exception.UserNotFound:
            pass
        else:
            msg = 'Duplicate name, %s.' % user['name']
            raise exception.Conflict(type='user', details=msg)

        user = utils.hash_user_password(user)
        new_user = user.copy()

        new_user.setdefault('groups', [])

        self.db.set('user-%s' % user_id, new_user)
        self.db.set('user_name-%s' % new_user['name'], new_user)
        user_list = set(self.db.get('user_list', []))
        user_list.add(user_id)
        self.db.set('user_list', list(user_list))
        return identity.filter_user(new_user)

    def update_user(self, user_id, user):
        if 'name' in user:
            user['name'] = clean.user_name(user['name'])
            existing = self.db.get('user_name-%s' % user['name'])
            if existing and user_id != existing['id']:
                msg = 'Duplicate name, %s.' % user['name']
                raise exception.Conflict(type='user', details=msg)
        # get the old name and delete it too
        try:
            old_user = self.db.get('user-%s' % user_id)
        except exception.NotFound:
            raise exception.UserNotFound(user_id=user_id)
        new_user = old_user.copy()
        user = utils.hash_user_password(user)
        new_user.update(user)
        if new_user['id'] != user_id:
            raise exception.ValidationError('Cannot change user ID')
        self.db.delete('user_name-%s' % old_user['name'])
        self.db.set('user-%s' % user_id, new_user)
        self.db.set('user_name-%s' % new_user['name'], new_user)
        return new_user

    def add_user_to_group(self, user_id, group_id):
        self.get_group(group_id)
        user_ref = self._get_user(user_id)
        groups = set(user_ref.get('groups', []))
        groups.add(group_id)
        self.update_user(user_id, {'groups': list(groups)})

    def check_user_in_group(self, user_id, group_id):
        self.get_group(group_id)
        user_ref = self._get_user(user_id)
        if not group_id in set(user_ref.get('groups', [])):
            raise exception.NotFound(_('User not found in group'))

    def remove_user_from_group(self, user_id, group_id):
        self.get_group(group_id)
        user_ref = self._get_user(user_id)
        groups = set(user_ref.get('groups', []))
        try:
            groups.remove(group_id)
        except KeyError:
            raise exception.NotFound(_('User not found in group'))
        self.update_user(user_id, {'groups': list(groups)})

    def list_users_in_group(self, group_id):
        self.get_group(group_id)
        user_keys = filter(lambda x: x.startswith("user-"), self.db.keys())
        user_refs = [self.db.get(key) for key in user_keys]
        user_refs_for_group = filter(lambda x: group_id in x['groups'],
                                     user_refs)
        return [identity.filter_user(x) for x in user_refs_for_group]

    def list_groups_for_user(self, user_id):
        user_ref = self._get_user(user_id)
        group_ids = user_ref.get('groups', [])
        return [self.get_group(x) for x in group_ids]

    def delete_user(self, user_id):
        try:
            old_user = self.db.get('user-%s' % user_id)
        except exception.NotFound:
            raise exception.UserNotFound(user_id=user_id)
        self.db.delete('user_name-%s' % old_user['name'])
        self.db.delete('user-%s' % user_id)
        user_list = set(self.db.get('user_list', []))
        user_list.remove(user_id)
        self.db.set('user_list', list(user_list))

    def create_project(self, tenant_id, tenant):
        tenant['name'] = clean.project_name(tenant['name'])
        try:
            self.get_project(tenant_id)
        except exception.ProjectNotFound:
            pass
        else:
            msg = 'Duplicate ID, %s.' % tenant_id
            raise exception.Conflict(type='tenant', details=msg)

        try:
            self.get_project_by_name(tenant['name'])
        except exception.ProjectNotFound:
            pass
        else:
            msg = 'Duplicate name, %s.' % tenant['name']
            raise exception.Conflict(type='tenant', details=msg)

        self.db.set('tenant-%s' % tenant_id, tenant)
        self.db.set('tenant_name-%s' % tenant['name'], tenant)
        return tenant

    def update_project(self, tenant_id, tenant):
        if 'name' in tenant:
            tenant['name'] = clean.project_name(tenant['name'])
            try:
                existing = self.db.get('tenant_name-%s' % tenant['name'])
                if existing and tenant_id != existing['id']:
                    msg = 'Duplicate name, %s.' % tenant['name']
                    raise exception.Conflict(type='tenant', details=msg)
            except exception.NotFound:
                pass
        # get the old name and delete it too
        try:
            old_project = self.db.get('tenant-%s' % tenant_id)
        except exception.NotFound:
            raise exception.ProjectNotFound(project_id=tenant_id)
        new_project = old_project.copy()
        new_project.update(tenant)
        new_project['id'] = tenant_id
        self.db.delete('tenant_name-%s' % old_project['name'])
        self.db.set('tenant-%s' % tenant_id, new_project)
        self.db.set('tenant_name-%s' % new_project['name'], new_project)
        return new_project

    def delete_project(self, tenant_id):
        try:
            old_project = self.db.get('tenant-%s' % tenant_id)
        except exception.NotFound:
            raise exception.ProjectNotFound(project_id=tenant_id)
        self.db.delete('tenant_name-%s' % old_project['name'])
        self.db.delete('tenant-%s' % tenant_id)

    def create_metadata(self, user_id, tenant_id, metadata,
                        domain_id=None, group_id=None):
        if user_id:
            self.db.set('metadata-%s-%s' % (tenant_id, user_id), metadata)
        else:
            self.db.set('metadata-%s-%s' % (tenant_id, group_id), metadata)
        return metadata

    def update_metadata(self, user_id, tenant_id, metadata,
                        domain_id=None, group_id=None):
        if user_id:
            self.db.set('metadata-%s-%s' % (tenant_id, user_id), metadata)
        else:
            self.db.set('metadata-%s-%s' % (tenant_id, group_id), metadata)
        return metadata

    def create_role(self, role_id, role):
        try:
            self.get_role(role_id)
        except exception.RoleNotFound:
            pass
        else:
            msg = 'Duplicate ID, %s.' % role_id
            raise exception.Conflict(type='role', details=msg)

        for role_ref in self.list_roles():
            if role['name'] == role_ref['name']:
                msg = 'Duplicate name, %s.' % role['name']
                raise exception.Conflict(type='role', details=msg)
        self.db.set('role-%s' % role_id, role)
        role_list = set(self.db.get('role_list', []))
        role_list.add(role_id)
        self.db.set('role_list', list(role_list))
        return role

    def update_role(self, role_id, role):
        old_role_ref = None
        for role_ref in self.list_roles():
            if role['name'] == role_ref['name'] and role_id != role_ref['id']:
                msg = 'Duplicate name, %s.' % role['name']
                raise exception.Conflict(type='role', details=msg)
            if role_id == role_ref['id']:
                old_role_ref = role_ref
        if old_role_ref is None:
            raise exception.RoleNotFound(role_id=role_id)
        new_role = old_role_ref.copy()
        new_role.update(role)
        new_role['id'] = role_id
        self.db.set('role-%s' % role_id, new_role)
        return role

    def delete_role(self, role_id):
        try:
            self.db.delete('role-%s' % role_id)
            metadata_keys = filter(lambda x: x.startswith("metadata-"),
                                   self.db.keys())
            for key in metadata_keys:
                tenant_id = key.split('-')[1]
                user_id = key.split('-')[2]
                try:
                    self.remove_role_from_user_and_project(user_id,
                                                           tenant_id,
                                                           role_id)
                except exception.RoleNotFound:
                    pass
        except exception.NotFound:
            raise exception.RoleNotFound(role_id=role_id)
        role_list = set(self.db.get('role_list', []))
        role_list.remove(role_id)
        self.db.set('role_list', list(role_list))

    def create_grant(self, role_id, user_id=None, group_id=None,
                     domain_id=None, project_id=None):

        self.get_role(role_id)
        if user_id:
            self.get_user(user_id)
        if group_id:
            self.get_group(group_id)
        if domain_id:
            self.get_domain(domain_id)
        if project_id:
            self.get_project(project_id)

        try:
            metadata_ref = self.get_metadata(user_id, project_id,
                                             domain_id, group_id)
        except exception.MetadataNotFound:
            metadata_ref = {}
        roles = set(metadata_ref.get('roles', []))
        roles.add(role_id)
        metadata_ref['roles'] = list(roles)
        self.update_metadata(user_id, project_id, metadata_ref,
                             domain_id, group_id)

    def list_grants(self, user_id=None, group_id=None,
                    domain_id=None, project_id=None):
        if user_id:
            self.get_user(user_id)
        if group_id:
            self.get_group(group_id)
        if domain_id:
            self.get_domain(domain_id)
        if project_id:
            self.get_project(project_id)

        try:
            metadata_ref = self.get_metadata(user_id, project_id,
                                             domain_id, group_id)
        except exception.MetadataNotFound:
            metadata_ref = {}
        return [self.get_role(x) for x in metadata_ref.get('roles', [])]

    def get_grant(self, role_id, user_id=None, group_id=None,
                  domain_id=None, project_id=None):
        self.get_role(role_id)
        if user_id:
            self.get_user(user_id)
        if group_id:
            self.get_group(group_id)
        if domain_id:
            self.get_domain(domain_id)
        if project_id:
            self.get_project(project_id)

        try:
            metadata_ref = self.get_metadata(user_id, project_id,
                                             domain_id, group_id)
        except exception.MetadataNotFound:
            metadata_ref = {}
        role_ids = set(metadata_ref.get('roles', []))
        if role_id not in role_ids:
            raise exception.RoleNotFound(role_id=role_id)
        return self.get_role(role_id)

    def delete_grant(self, role_id, user_id=None, group_id=None,
                     domain_id=None, project_id=None):
        self.get_role(role_id)
        if user_id:
            self.get_user(user_id)
        if group_id:
            self.get_group(group_id)
        if domain_id:
            self.get_domain(domain_id)
        if project_id:
            self.get_project(project_id)

        try:
            metadata_ref = self.get_metadata(user_id, project_id,
                                             domain_id, group_id)
        except exception.MetadataNotFound:
            metadata_ref = {}
        roles = set(metadata_ref.get('roles', []))
        try:
            roles.remove(role_id)
        except KeyError:
            raise exception.RoleNotFound(role_id=role_id)
        metadata_ref['roles'] = list(roles)
        self.update_metadata(user_id, project_id, metadata_ref,
                             domain_id, group_id)

    # domain crud

    def create_domain(self, domain_id, domain):
        self.db.set('domain-%s' % domain_id, domain)
        domain_list = set(self.db.get('domain_list', []))
        domain_list.add(domain_id)
        self.db.set('domain_list', list(domain_list))
        return domain

    def list_domains(self):
        return self.db.get('domain_list', [])

    def get_domain(self, domain_id):
        return self.db.get('domain-%s' % domain_id)

    def update_domain(self, domain_id, domain):
        self.db.set('domain-%s' % domain_id, domain)
        return domain

    def delete_domain(self, domain_id):
        self.db.delete('domain-%s' % domain_id)
        domain_list = set(self.db.get('domain_list', []))
        domain_list.remove(domain_id)
        self.db.set('domain_list', list(domain_list))

    # group crud

    def create_group(self, group_id, group):
        self.db.set('group-%s' % group_id, group)
        group_list = set(self.db.get('group_list', []))
        group_list.add(group_id)
        self.db.set('group_list', list(group_list))
        return group

    def list_groups(self):
        return self.db.get('group_list', [])

    def get_group(self, group_id):
        try:
            return self.db.get('group-%s' % group_id)
        except exception.NotFound:
            raise exception.GroupNotFound(group_id=group_id)

    def update_group(self, group_id, group):
        self.db.set('group-%s' % group_id, group)
        return group

    def delete_group(self, group_id):
        # Delete any entries in the group lists of all users
        user_keys = filter(lambda x: x.startswith("user-"), self.db.keys())
        user_refs = [self.db.get(key) for key in user_keys]
        for user_ref in user_refs:
            groups = set(user_ref.get('groups', []))
            if group_id in groups:
                groups.remove(group_id)
                self.update_user(user_ref['id'], {'groups': list(groups)})

        # Now delete the group itself
        self.db.delete('group-%s' % group_id)
        group_list = set(self.db.get('group_list', []))
        group_list.remove(group_id)
        self.db.set('group_list', list(group_list))
