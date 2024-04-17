"""
Copyright start
MIT License
Copyright (c) 2024 Fortinet Inc
Copyright end
"""

import json
from os.path import join

import requests
import xmltodict
from connectors.core.connector import get_logger, ConnectorError
from connectors.cyops_utilities.builtins import download_file_from_cyops
from connectors.cyops_utilities.builtins import upload_file_to_cyops
from django.conf import settings
from integrations.crudhub import make_request
from connectors.cyops_utilities.builtins import save_file_in_env

logger = get_logger('azure-blob-storage')


class AzureBlobStorage(object):
    def __init__(self, config, params):
        self.account_name = config.get('account_name')
        self.sas_token = str(config.get('sas_token'))
        if self.sas_token and not self.sas_token.startswith("?"):
            self.sas_token = f"?{self.sas_token}"
        container_name = params.pop('container_name', '')
        self.container_name = container_name if container_name else config.get('container_name')
        self.verify_ssl = config.get('verify_ssl')
        self.storage_service_endpoint = f'https://{self.account_name}.blob.core.windows.net/{self.container_name}'
        self.azure_storage_endpoint = f'https://{self.account_name}.blob.core.windows.net'

    def make_rest_api(self, method, endpoint, params={}, query_string='', data=None, verify_ssl=False, headers={},
                      return_header_response=False, return_file_content=False, destination_endpoint=''):
        try:
            headers['Content-Type'] = 'application/json'
            headers['Accept'] = 'application/json'
            if destination_endpoint:
                blob_storage_service_endpoint = destination_endpoint
            else:
                if endpoint:
                    blob_storage_service_endpoint = f'{self.storage_service_endpoint}{endpoint}'
                else:
                    blob_storage_service_endpoint = f'{self.storage_service_endpoint}'
            blob_storage_service_endpoint += self.sas_token
            if query_string:
                blob_storage_service_endpoint += '&' + query_string
            logger.debug('Rest API Endpoint: {}'.format(blob_storage_service_endpoint))
            response = requests.request(method, blob_storage_service_endpoint, headers=headers, params=params,
                                        data=data, verify=verify_ssl)
            logger.debug('Rest API Status Code: {}'.format(response.status_code))
            if response.ok:
                if return_header_response:
                    return response.headers
                content_type = response.headers.get('Content-Type')
                if return_file_content:
                    return response.content
                elif response.text != "" and 'application/xml' in content_type:
                    return json.loads(json.dumps(xmltodict.parse(response.content.decode('utf-8'))))
                elif response.text != "" and 'application/json' in content_type:
                    return response.json()
                else:
                    return response.content
            elif response.status_code==409:
                return json.loads(json.dumps(xmltodict.parse(response.content.decode('utf-8'))))
            else:
                raise ConnectorError("{0}".format(response.content))
        except requests.exceptions.SSLError:
            raise ConnectorError('SSL certificate validation failed')
        except requests.exceptions.ConnectTimeout:
            raise ConnectorError('The request timed out while trying to connect to the server')
        except requests.exceptions.ReadTimeout:
            raise ConnectorError(
                'The server did not send any data in the allotted amount of time')
        except requests.exceptions.ConnectionError:
            raise ConnectorError('Invalid Credentials')
        except Exception as err:
            raise ConnectorError(str(err))


def _get_file_iri(params):
    file_id = params.get('value')
    iri_type = 'attachment'
    file_name = None
    if not file_id.startswith('/api/3/'):
        file_id = '/api/3/attachments/' + file_id
    elif file_id.startswith('/api/3/files'):
        iri_type = 'file'

    if iri_type == 'attachment':
        attachment_data = make_request(file_id, 'GET')
        file_iri = attachment_data['file']['@id']
    else:
        file_iri = file_id
    return file_iri


def get_file_file_content(file_iri, **kwargs):
    try:
        env = kwargs.get('env', {})
        file_path = join('/tmp', download_file_from_cyops(file_iri)['cyops_file_path'])
        logger.info(file_path)
        with open(file_path, 'rb') as attachment:
            file_data = attachment.read()
        save_file_in_env(env, file_path)
        if file_data:
            return file_data
        raise ConnectorError('The file must not be empty')
    except Exception as Err:
        logger.error('Error in submitFile(): %s' % Err)
        raise ConnectorError('Error in submitFile(): %s' % Err)


def create_blob(config, params, **kwargs):
    headers = dict()
    az_blob = AzureBlobStorage(config, params)
    file_iri = _get_file_iri(params)
    logger.debug('File IRI: {}'.format(file_iri))
    file_content = get_file_file_content(file_iri, **kwargs)
    headers["x-ms-blob-type"] = 'BlockBlob'
    headers["x-ms-blob-content-disposition"] = 'application/octet-stream'
    endpoint = f"/{params.get('blob_name')}"
    resp = az_blob.make_rest_api("PUT", endpoint, data=file_content, headers=headers, return_header_response=True)
    resp.update({'status': 'success', 'result': 'Blob successfully uploaded.'})
    return resp



def list_blob(config, params, **kwargs):
    az_blob = AzureBlobStorage(config, params)
    return az_blob.make_rest_api("GET", '', query_string='restype=container&comp=list')


def get_blob(config, params, **kwargs):
    env = kwargs.get('env', {})
    az_blob = AzureBlobStorage(config, params)
    blob_name = params.pop('blob_name', '')
    endpoint = f'/{blob_name}'
    params['snapshot']='2024-03-05'
    blob_content = az_blob.make_rest_api("GET", endpoint, return_file_content=True)
    path = join(settings.TMP_FILE_ROOT, blob_name)
    with open(path, 'wb') as fp:
        fp.write(blob_content)
    attach_response = upload_file_to_cyops(file_path=blob_name, filename=blob_name, name=blob_name, create_attachment=True)
    save_file_in_env(env, blob_name)
    return attach_response


def copy_blob(config, params, **kwargs):
    headers = dict()
    az_blob = AzureBlobStorage(config, params)
    source_container_name = params.pop('source_container_name', '')
    destination_container_name = params.pop('destination_container_name', '')
    blob_name = params.pop('blob_name', '')
    sas_token = config.get('sas_token')
    source_endpoint = f"{az_blob.azure_storage_endpoint}/{source_container_name}/{blob_name}?{sas_token}"
    destination_endpoint = f"{az_blob.azure_storage_endpoint}/{destination_container_name}/{blob_name}"
    headers['x-ms-copy-source'] = source_endpoint
    return az_blob.make_rest_api("PUT", destination_endpoint, headers=headers,
                                 destination_endpoint=destination_endpoint, return_header_response=True)


def delete_blob(config, params, **kwargs):
    az_blob = AzureBlobStorage(config, params)
    blob_name = params.pop('blob_name', '')
    endpoint = f'/{blob_name}'
    return az_blob.make_rest_api("DELETE", endpoint, config, return_header_response=True)


def abort_copy_blob(config, params, **kwargs):
    headers = dict()
    az_blob = AzureBlobStorage(config, params)
    blob_name = params.pop('blob_name', '')
    copy_id = params.pop('copy_id', '')
    query_string = f'comp=copy&copyid={copy_id}'
    endpoint = f'/{blob_name}'
    headers['x-ms-copy-action'] = 'abort'
    return az_blob.make_rest_api("PUT", endpoint, config, query_string=query_string, headers=headers)


def get_blob_properties(config, params, **kwargs):
    az_blob = AzureBlobStorage(config, params)
    blob_name = params.get('blob_name')
    endpoint = f'/{blob_name}'
    return az_blob.make_rest_api("HEAD", endpoint, config, return_header_response=True)


def get_blob_metadata(config, params, **kwargs):
    az_blob = AzureBlobStorage(config, params)
    blob_name = params.get('blob_name')
    endpoint = f'/{blob_name}'
    return az_blob.make_rest_api("GET", endpoint, config, query_string='comp=metadata', return_header_response=True)


def get_blob_tags(config, params, **kwargs):
    az_blob = AzureBlobStorage(config, params)
    blob_name = params.get('blob_name')
    endpoint = f'/{blob_name}'
    return az_blob.make_rest_api("GET", endpoint, config, query_string='comp=tags')


def _check_health(config):
    try:
        if list_blob(config, {}):
            return True
        else:
            raise ConnectorError("Invalid Credentials")
    except Exception as err:
        logger.error(err)
        raise ConnectorError(str(err))


operations = {
    'create_blob': create_blob,
    'list_blob': list_blob,
    'get_blob': get_blob,
    'copy_blob': copy_blob,
    'delete_blob': delete_blob,
    'abort_copy_blob': abort_copy_blob,
    'get_blob_properties': get_blob_properties,
    'get_blob_metadata': get_blob_metadata,
    'get_blob_tags': get_blob_tags
}

