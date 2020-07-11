#!/usr/bin/python

# Copyright: (c) 2020, Rhys Campbell <rhys.james.campbell@googlemail.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type


DOCUMENTATION = r'''
---
module: mongodb_oplog
short_description: Resizes the MongoDB oplog.
description:
  - Resizes the MongoDB oplog.
  - This module should only be used with MongoDB 3.6 and above.
  - Old MongoDB versions should use an alternative method.
  - Consult U(https://docs.mongodb.com/manual/tutorial/change-oplog-size) for further info.
author: Rhys Campbell (@rhysmeister)
options:
  login_user:
    description:
    - The username to authenticate with.
    - If auth is not enabled do not supply this value.
    type: str
  login_password:
    description:
    - The password to authenticate with.
    - If auth is not enabled do not supply this value.
    type: str
  login_database:
    description:
    - The database where login credentials are stored.
    type: str
    default: admin
  login_host:
    description:
    - The MongoDB hostname.
    type: str
    default: localhost
  login_port:
    description:
    - The MongoDB port to login to.
    type: int
    default: 27017
  oplog_size_mb:
    description:
      - Desired new size on MB of the oplog.
    type: int
    required: true
  compact:
    description:
      - Runs compact against the oplog.rs collection in the local database to reclaim disk space.
      - Will not run against PRIMARY members.
      - The MongoDB user must have the compact role on the local database for this feature to work.
    type: bool
    default: false
    required: false
  ssl:
    description:
      - Whether to use an SSL connection when connecting to the database.
    default: False
    type: bool
  ssl_cert_reqs:
    description:
    - Specifies whether a certificate is required from the other side of the connection, and whether it will be validated if provided.
    type: str
    default: CERT_REQUIRED
    choices: [ CERT_NONE, CERT_OPTIONAL, CERT_REQUIRED ]
notes:
- Requires the pymongo Python package on the remote host, version 2.4.2+. This
  can be installed using pip or the OS package manager. @see U(http://api.mongodb.org/python/current/installation.html)
requirements:
- pymongo
'''

EXAMPLES = r'''
- name: Resize oplog to 16 gigabytes, or 16000 megabytes
  mongodb_oplog:
    oplog_size_mb:  16000

- name: Resize oplog to 8 gigabytes and compact of secondaries to reclaim space
  mongodb_maintenance:
    oplog_size_mb:  8000
    compact: true
'''

RETURN = r'''
changed:
  description: Whether the member oplog was modified.
  returned: success
  type: bool
compacted:
  description: Whether the member oplog was compacted.
  returned: success
  type: bool
msg:
  description: A short description of what happened.
  returned: success
  type: str
failed:
  description: If something went wrong
  returned: failed
  type: bool
'''

from copy import deepcopy

import os
import ssl as ssl_lib
from distutils.version import LooseVersion


from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six import binary_type, text_type
from ansible.module_utils.six.moves import configparser
from ansible.module_utils._text import to_native
from ansible_collections.community.mongodb.plugins.module_utils.mongodb_common import check_compatibility, missing_required_lib, load_mongocnf, member_state
from ansible_collections.community.mongodb.plugins.module_utils.mongodb_common import PyMongoVersion, PYMONGO_IMP_ERR, pymongo_found, MongoClient


def get_olplog_size(client):
    return int(client["local"].command("collStats", "oplog.rs")["maxSize"]) / 1024 / 1024


def set_oplog_size(client, oplog_size_mb):
    client["admin"].command({"replSetResizeOplog": 1, "size": oplog_size_mb})


def compact_oplog(client):
    client["local"].command("compact", "oplog.rs")


def main():
    module = AnsibleModule(
        argument_spec=dict(
            login_user=dict(type='str'),
            login_password=dict(type='str', no_log=True),
            login_database=dict(type='str', default="admin"),
            login_host=dict(type='str', default="localhost"),
            login_port=dict(type='int', default=27017),
            oplog_size_mb=dict(type='int', required=True),
            compact=dict(type='bool', default=False),
            ssl=dict(type='bool', default=False),
            ssl_cert_reqs=dict(type='str', default='CERT_REQUIRED', choices=['CERT_NONE', 'CERT_OPTIONAL', 'CERT_REQUIRED']),
        ),
        supports_check_mode=True,
    )

    if not pymongo_found:
        module.fail_json(msg=missing_required_lib('pymongo'),
                         exception=PYMONGO_IMP_ERR)

    login_user = module.params['login_user']
    login_password = module.params['login_password']
    login_database = module.params['login_database']
    login_host = module.params['login_host']
    login_port = module.params['login_port']
    oplog_size_mb = module.params['oplog_size_mb']
    compact = module.params['compact']
    ssl = module.params['ssl']

    result = dict(
        changed=False,
    )

    connection_params = dict(
        host=login_host,
        port=int(login_port),
    )

    if ssl:
        connection_params["ssl"] = ssl
        connection_params["ssl_cert_reqs"] = getattr(ssl_lib, module.params['ssl_cert_reqs'])

    try:
        client = MongoClient(**connection_params)
    except Exception as excep:
        module.fail_json(msg='Unable to connect to MongoDB: %s' % to_native(excep))

    if login_user is None and login_password is None:
        mongocnf_creds = load_mongocnf()
        if mongocnf_creds is not False:
            login_user = mongocnf_creds['user']
            login_password = mongocnf_creds['password']
    elif login_password is None or login_user is None:
        module.fail_json(msg="When supplying login arguments, both 'login_user' and 'login_password' must be provided")

    if login_user is not None and login_password is not None:
        try:
            client.admin.authenticate(login_user, login_password, source=login_database)
            # Get server version:
            try:
                srv_version = LooseVersion(client.server_info()['version'])
            except Exception as excep:
                module.fail_json(msg='Unable to get MongoDB server version: %s' % to_native(excep))

            # Get driver version::
            driver_version = LooseVersion(PyMongoVersion)
            # Check driver and server version compatibility:
            check_compatibility(module, srv_version, driver_version)
        except Exception as excep:
            module.fail_json(msg='Unable to authenticate with MongoDB: %s' % to_native(excep))

        try:
            current_oplog_size = get_olplog_size(client)
        except Exception as excep:
            module.fail_json(msg='Unable to get current oplog size: %s' % to_native(excep))
        if oplog_size_mb == current_oplog_size:
            result["msg"] = "oplog_size_mb is already {0} mb".format(oplog_size_mb)
            result["compacted"] = False
        else:
            try:
                state = member_state(client)
            except Exception as excep:
                module.fail_json(msg='Unable to get member state: %s' % to_native(excep))
            if module.check_mode:
                result["changed"] = True
                result["msg"] = "oplog has been resized from {0} mb to {1} mb".format(round(current_oplog_size, 0),
                                                                                      round(oplog_size_mb, 0))
                if state == "SECONDARY" and compact:
                    result["compacted"] = True
                else:
                    result["compacted"] = False
            else:
                try:
                    set_oplog_size(client, oplog_size_mb)
                    result["changed"] = True
                    result["msg"] = "oplog has been resized from {0} mb to {1} mb".format(round(current_oplog_size, 0),
                                                                                          round(oplog_size_mb, 0))
                except Exception as excep:
                    module.fail_json(msg='Unable to set oplog size: %s' % to_native(excep))
                if state == "SECONDARY" and compact and current_oplog_size > oplog_size_mb:
                    try:
                        compact_oplog(client)
                        result["compacted"] = True
                    except Exception as excep:
                        module.fail_json(msg='Error compacting member oplog: %s' % to_native(excep))
                else:
                    result["compacted"] = False

    module.exit_json(**result)


if __name__ == '__main__':
    main()