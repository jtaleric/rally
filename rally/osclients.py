# Copyright 2013: Mirantis Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import urlparse

from ceilometerclient import client as ceilometer
from cinderclient import client as cinder
import glanceclient as glance
from heatclient import client as heat
from ironicclient import client as ironic
from keystoneclient import exceptions as keystone_exceptions
from keystoneclient.v2_0 import client as keystone
from neutronclient.neutron import client as neutron
from novaclient import client as nova
from oslo.config import cfg
from saharaclient import client as sahara

from rally import consts
from rally import exceptions


CONF = cfg.CONF
CONF.register_opts([
    cfg.FloatOpt("openstack_client_http_timeout", default=180.0,
                 help="HTTP timeout for any of OpenStack service in seconds"),
    cfg.BoolOpt("https_insecure", default=False,
                help="Use SSL for all OpenStack API interfaces"),
    cfg.StrOpt("https_cacert", default=None,
               help="Path to CA server cetrificate for SSL")
])


# NOTE(boris-42): super dirty hack to fix nova python client 2.17 thread safe
nova._adapter_pool = lambda x: nova.adapters.HTTPAdapter()


def cached(func):
    """Cache client handles."""
    def wrapper(self, *args, **kwargs):
        key = '{0}{1}{2}'.format(func.__name__,
                                 str(args) if args else '',
                                 str(kwargs) if kwargs else '')

        if key in self.cache:
            return self.cache[key]
        self.cache[key] = func(self, *args, **kwargs)
        return self.cache[key]
    return wrapper


class Clients(object):
    """This class simplify and unify work with openstack python clients."""

    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.cache = {}

    def clear(self):
        """Remove all cached client handles."""
        self.cache = {}

    @cached
    def keystone(self):
        """Return keystone client."""
        new_kw = {
            "timeout": CONF.openstack_client_http_timeout,
            "insecure": CONF.https_insecure, "cacert": CONF.https_cacert
        }
        kw = dict(self.endpoint.to_dict().items() + new_kw.items())
        if kw["use_public_urls"]:
            mgmt_url = urlparse.urlparse(kw["auth_url"])
            if mgmt_url.port != kw["admin_port"]:
                kw["endpoint"] = "{0}://{1}:{2}{3}".format(
                    mgmt_url.scheme,
                    mgmt_url.hostname,
                    kw["admin_port"],
                    mgmt_url.path
                )
            else:
                kw["endpoint"] = kw["auth_url"]
        client = keystone.Client(**kw)
        client.authenticate()
        return client

    def verified_keystone(self):
        """Ensure keystone endpoints are valid and then authenticate

        :returns: Keystone Client
        """
        try:
            # Ensure that user is admin
            client = self.keystone()
            roles = client.auth_ref['user']['roles']
            if not any('admin' == role['name'] for role in roles):
                raise exceptions.InvalidAdminException(
                    username=self.endpoint.username)
        except keystone_exceptions.Unauthorized:
            raise exceptions.InvalidEndpointsException()
        except keystone_exceptions.AuthorizationFailure:
            raise exceptions.HostUnreachableException(
                url=self.endpoint.auth_url)
        return client

    @cached
    def nova(self, version='2'):
        """Return nova client."""
        client = nova.Client(version,
                             self.endpoint.username,
                             self.endpoint.password,
                             self.endpoint.tenant_name,
                             auth_url=self.endpoint.auth_url,
                             region_name=self.endpoint.region_name,
                             service_type='compute',
                             http_log_debug=CONF.debug,
                             timeout=CONF.openstack_client_http_timeout,
                             insecure=CONF.https_insecure,
                             cacert=CONF.https_cacert)
        return client

    @cached
    def neutron(self, version='2.0'):
        """Return neutron client."""
        client = neutron.Client(version,
                                username=self.endpoint.username,
                                password=self.endpoint.password,
                                tenant_name=self.endpoint.tenant_name,
                                auth_url=self.endpoint.auth_url,
                                region_name=self.endpoint.region_name,
                                timeout=CONF.openstack_client_http_timeout,
                                insecure=CONF.https_insecure,
                                cacert=CONF.https_cacert)
        return client

    @cached
    def glance(self, version='1'):
        """Return glance client."""
        kc = self.keystone()
        endpoint = kc.service_catalog.get_endpoints()['image'][0]
        client = glance.Client(version,
                               endpoint=endpoint['publicURL'],
                               token=kc.auth_token,
                               region_name=self.endpoint.region_name,
                               timeout=CONF.openstack_client_http_timeout,
                               insecure=CONF.https_insecure,
                               cacert=CONF.https_cacert)
        return client

    @cached
    def heat(self, version='1'):
        """Return heat client."""
        kc = self.keystone()
        endpoint = kc.service_catalog.get_endpoints()['orchestration'][0]

        client = heat.Client(version,
                             endpoint=endpoint['publicURL'],
                             token=kc.auth_token,
                             region_name=self.endpoint.region_name,
                             timeout=CONF.openstack_client_http_timeout,
                             insecure=CONF.https_insecure,
                             cacert=CONF.https_cacert)
        return client

    @cached
    def cinder(self, version='1'):
        """Return cinder client."""
        client = cinder.Client(version,
                               self.endpoint.username,
                               self.endpoint.password,
                               self.endpoint.tenant_name,
                               auth_url=self.endpoint.auth_url,
                               region_name=self.endpoint.region_name,
                               service_type='volume',
                               http_log_debug=CONF.debug,
                               timeout=CONF.openstack_client_http_timeout,
                               insecure=CONF.https_insecure,
                               cacert=CONF.https_cacert)
        return client

    @cached
    def ceilometer(self, version='2'):
        """Return ceilometer client."""
        kc = self.keystone()
        endpoint = kc.service_catalog.get_endpoints()['metering'][0]
        auth_token = kc.auth_token
        if not hasattr(auth_token, '__call__'):
            # python-ceilometerclient requires auth_token to be a callable
            auth_token = lambda: kc.auth_token

        client = ceilometer.Client(version,
                                   endpoint=endpoint['publicURL'],
                                   token=auth_token,
                                   region_name=self.endpoint.region_name,
                                   timeout=CONF.openstack_client_http_timeout,
                                   insecure=CONF.https_insecure,
                                   cacert=CONF.https_cacert)
        return client

    @cached
    def ironic(self, version='1.0'):
        """Return Ironic client."""
        client = ironic.Client(version,
                               username=self.endpoint.username,
                               password=self.endpoint.password,
                               tenant_name=self.endpoint.tenant_name,
                               auth_url=self.endpoint.auth_url,
                               timeout=CONF.openstack_client_http_timeout,
                               insecure=CONF.https_insecure,
                               cacert=CONF.https_cacert)
        return client

    @cached
    def sahara(self, version='1.1'):
        """Return Sahara client."""
        client = sahara.Client(version,
                               username=self.endpoint.username,
                               api_key=self.endpoint.password,
                               project_name=self.endpoint.tenant_name,
                               auth_url=self.endpoint.auth_url)

        return client

    @cached
    def services(self):
        """Return available services names and types.

        :returns: dict, {"service_type": "service_name", ...}
        """
        services_data = {}
        available_services = self.keystone().service_catalog.get_endpoints()
        for service_type in available_services.keys():
            if service_type in consts.ServiceType:
                services_data[service_type] = consts.ServiceType[service_type]
        return services_data
