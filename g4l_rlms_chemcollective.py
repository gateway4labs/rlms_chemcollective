# -*-*- encoding: utf-8 -*-*-

import os
import re
import sys
import time
import sys
import urlparse
import json
import datetime
import uuid
import hashlib
import threading
import Queue
import functools
import traceback
import pprint

import requests
from bs4 import BeautifulSoup

from flask import Blueprint, request, url_for
from flask.ext.wtf import TextField, PasswordField, Required, URL, ValidationError

from labmanager.forms import AddForm
from labmanager.rlms import register, Laboratory, CacheDisabler, LabNotFoundError, register_blueprint
from labmanager.rlms.base import BaseRLMS, BaseFormCreator, Capabilities, Versions
from labmanager.rlms.queue import QueueTask, run_tasks

    
def dbg(msg):
    if DEBUG:
        print "[%s]" % time.asctime(), msg
        sys.stdout.flush()

def dbg_lowlevel(msg, scope):
    if DEBUG_LOW_LEVEL:
        print "[%s][%s][%s]" % (time.asctime(), threading.current_thread().name, scope), msg
        sys.stdout.flush()


class ChemCollectiveAddForm(AddForm):

    DEFAULT_URL = 'http://www.chemcollective.org'
    DEFAULT_LOCATION = 'United States'
    DEFAULT_PUBLICLY_AVAILABLE = True
    DEFAULT_PUBLIC_IDENTIFIER = 'chemcollective'
    DEFAULT_AUTOLOAD = True

    def __init__(self, add_or_edit, *args, **kwargs):
        super(ChemCollectiveAddForm, self).__init__(*args, **kwargs)
        self.add_or_edit = add_or_edit

    @staticmethod
    def process_configuration(old_configuration, new_configuration):
        return new_configuration


class ChemCollectiveFormCreator(BaseFormCreator):

    def get_add_form(self):
        return ChemCollectiveAddForm

MIN_TIME = datetime.timedelta(hours=24)

def get_languages():
    return ['en'] # 'it', 'es']

def get_laboratories():
    labs_and_identifiers  = CHEMCOLLECTIVE.cache.get('get_laboratories',  min_time = MIN_TIME)
    if labs_and_identifiers:
        labs, identifiers = labs_and_identifiers
        return labs, identifiers

    index = requests.get('http://chemcollective.org/vlabs').text
    soup = BeautifulSoup(index, 'lxml')


    identifiers = {
        # identifier: {
        #     'name': name,
        #     'link': link,
        #     'message': (message)
        # }
    }

    for link in soup.find_all("a", class_="go"):
        href = link['href'].replace('activities/', '')
        name = link.find_parent("li").find("h4").text.strip()
        identifier = href.replace('vlab/', '')
        external_link = 'http://chemcollective.org/' + link['href']
        link = url_for('chemcollective.chemcollective_get', lang='LANG', identifier=identifier, _external=True)

        lab_contents = requests.get(external_link).text
        message_str = lab_contents.split("message =")[1].split("}")[0].split('{')[1]
        message_str = '{ %s }' % message_str

        identifiers[identifier] = {
            'name': name,
            'link': link,
            'message': message_str,
        }

    labs = []
    for identifier, identifier_data in identifiers.items():
        name = identifier_data['name']
        lab = Laboratory(name=name, laboratory_id=identifier, description=name)
        labs.append(lab)

    CHEMCOLLECTIVE.cache['get_laboratories'] = (labs, identifiers)
    return labs, identifiers

FORM_CREATOR = ChemCollectiveFormCreator()

CAPABILITIES = [ Capabilities.WIDGET, Capabilities.URL_FINDER, Capabilities.CHECK_URLS ]

class RLMS(BaseRLMS):

    def __init__(self, configuration, *args, **kwargs):
        self.configuration = json.loads(configuration or '{}')

    def get_version(self):
        return Versions.VERSION_1

    def get_capabilities(self):
        return CAPABILITIES

    def get_laboratories(self, **kwargs):
        labs, identifiers = get_laboratories()
        return labs

    def get_base_urls(self):
        return [ 'http://chemcollective.org', 'https://chemcollective.org', 'http://www.chemcollective.org', 'http://www.chemcollective.org' ]

    def get_lab_by_url(self, url):
        results = url.rsplit('/vlab/', 1)
        if len(results) == 1:
            return None

        identifier = results[1].split('?')[0]

        laboratories, identifiers = get_laboratories()
        for lab in laboratories:
            if lab.laboratory_id == identifier:
                return lab

        return None

    def get_check_urls(self, laboratory_id):
        laboratories, identifiers = get_laboratories()
        lab_data = identifiers.get(laboratory_id)
        if lab_data:
            return [ lab_data['link'] ]
        return []

    def reserve(self, laboratory_id, username, institution, general_configuration_str, particular_configurations, request_payload, user_properties, *args, **kwargs):
        laboratories, identifiers = get_laboratories()
        if laboratory_id not in identifiers:
            raise LabNotFoundError("Laboratory not found: {}".format(laboratory_id))

        url = identifiers[laboratory_id]['link']

        lang = 'en'
        if 'locale' in kwargs:
            lang = kwargs['locale']
            if lang not in get_languages():
                lang = 'en'

        url = url.replace('LANG', lang)

        response = {
            'reservation_id' : url,
            'load_url' : url,
        }
        return response


    def load_widget(self, reservation_id, widget_name, **kwargs):
        return {
            'url' : reservation_id
        }

    def list_widgets(self, laboratory_id, **kwargs):
        default_widget = dict( name = 'default', description = 'Default widget' )
        return [ default_widget ]


class VirtualBiologyLabTaskQueue(QueueTask):
    RLMS_CLASS = RLMS

def populate_cache(rlms):
    rlms.get_laboratories()

chemcollective_blueprint = Blueprint('chemcollective', __name__)

@chemcollective_blueprint.route('/lang/<lang>/id/<identifier>')
def chemcollective_get(lang, identifier):
    laboratories, identifiers = get_laboratories()
    if identifier not in identifiers:
        raise LabNotFoundError("Laboratory not found: {}".format(identifier))

    message = identifiers[identifier]['message']

    if lang not in get_languages():
        lang = 'en'

    message = message.replace('language: "en"', 'language: "{}"'.format(lang))

    return """<html>
    <body style="border: 0; margin: 0">
    <script src="http://chemcollective.org/assets/modules/activities/autograded_problems/common.js"></script>
    <script>
        var session= numTimestamp()+"_"+intRandom(1000000,9999999);
        var userID=""+Math.floor(Math.random()*100000000);

        var message = """ + message + """;


        window.onload = function() {
            var labframe = document.getElementById("labframe").contentWindow;
            labframe.postMessage(message, "http://chemcollective.org");
        };
    </script>

    <iframe id="labframe" border="0" src="http://chemcollective.org/chem/jsvlab/vlab.html" style="min-width:1000px; width: 100%; height: 100%; min-height:650px; border-size: 0px"></iframe>
</body>
</html>
"""

register_blueprint(chemcollective_blueprint, url='/chemcollective')

CHEMCOLLECTIVE = register("ChemCollective", ['1.0'], __name__)
CHEMCOLLECTIVE.add_local_periodic_task('Populating cache', populate_cache, hours = 23)

DEBUG = CHEMCOLLECTIVE.is_debug() or (os.environ.get('G4L_DEBUG') or '').lower() == 'true' or False
DEBUG_LOW_LEVEL = DEBUG and (os.environ.get('G4L_DEBUG_LOW') or '').lower() == 'true'

if DEBUG:
    print("Debug activated")

if DEBUG_LOW_LEVEL:
    print("Debug low level activated")

sys.stdout.flush()

if __name__ == '__main__':
    from labmanager import app
    with app.app_context():
            global url_for
            url_for = lambda *args, **kwargs: 'link'
            laboratories, identifiers = get_laboratories()

            for identifier_data in identifiers.values():
                basic_urls = [ line.split('"')[1] for line in identifier_data['message'].splitlines() if 'assignmentPath' in line ]
                if not basic_urls:
                    continue
                basic_url = basic_urls[0]
                print(basic_url)
                import requests
                r = requests.get("http://chemcollective.org/chem/jsvlab/scripts/resources/assignments/it/" + basic_url + "/configuration.json")
                if r.status_code == 404:
                    print("NOT FOUND")
                else:
                    print("FOUND: ", r.status_code)


    rlms = RLMS('{}')
    labs = rlms.get_laboratories()
    for lab in labs:
        print rlms.reserve(lab.laboratory_id, 'nobody', 'nowhere', '{}', [], {}, {})
