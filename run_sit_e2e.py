import logging
import json
import time
import urllib.parse
import datetime

import argparse
import requests

TESTS_TO_EXECUTE = ['run_health_checks',
                    'run_sanity_check',
                    'run_casi_sanity',
                    'run_casi_resolve_stats_report',
                    'run_tyk_health_check',
                    'run_identities_upsert_via_pipeline',
                    'run_identities_upsert_for_new_tenant',
                    'run_umbrella_end_to_end_v2_test',
                    'run_cdfw_end_to_end_test'
                    # 'run_app_discovery_api_availability'
                    ]

SUCCESS_STATUS = 'succeeded'
RUNNING_STATUS = 'running'
FAILED_STATUS = 'failed'
NUM_OF_WAIT_ITER = 15
SLEEP_TIME = 30
E2E_START_LABEL = 'E2E START TIME'
E2E_ELAPSED_LABEL = 'E2E ELAPSED TIME'
E2E_ENDED = 'E2E RUN ENDED'
E2E_RUN_FAILED = 'E2E RUN FAILED'
SLACK_HOOK = "https://hooks.slack.com/services/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"

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


class E2eTest(object):
    def __init__(self, token, cluster, domain):
        self.token = token
        if len(domain) < 1:
            domain = "cloudlockng.com"
        self.base_url = 'https://{}/e2e/api/v1/'.format(domain)
        self.scenario_name = None
        self.run_id = None
        self.status = {}

    def run(self, test_name):
        self.scenario_name = test_name
        return self._run_test(test_name)

    def _run_test(self, test_name):
        result = False
        url = urllib.parse.urljoin(self.base_url, 'execute/{test_name}'.format(test_name=test_name))
        logger.info('url %s', url)
        headers = {'authorization': 'Bearer {}'.format(self.token)}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            logger.info('Successfully init the test: %s', test_name)
            self.run_id = response.json()['id']
            if response.json()['Success']:
                result = True
            else:
                logger.error('Failure - Got bad status for test: %s', test_name)
        else:
            logger.error('Failed init the test: %s response: %s', test_name, response.json())

        return result

    def get_status(self):
        url = urllib.parse.urljoin(self.base_url, 'admin/status/{}'.format(self.run_id))
        headers = {'authorization': 'Bearer {}'.format(self.token)}
        response = requests.get(url, headers=headers)
        logger.info('Get status for run_id: %s', self.run_id)
        logger.info('url %s', url)
        logger.info('Headers, response: %s %s', headers, response.json())
        if response.status_code == 200:
            self.status = response.json()


class E2eTests(object):
    def __init__(self, token, cluster, domain):
        self.token = token
        self.domain = domain
        self.cluster = cluster
        self.all_tests = []
        self.start_time = None
        self.did_succeed = False

    @staticmethod
    def mark_time(label, dt_obj):
        logger.info('-' * 40)
        logger.info('E2E - %s - %s', label, str(dt_obj))
        logger.info('-' * 40)

    @staticmethod
    def get_time_now():
        return datetime.datetime.utcnow().replace(microsecond=0)

    def on_end(self):
        elapsed_time = self.get_time_now() - self.start_time
        self.mark_time(E2E_ELAPSED_LABEL, elapsed_time)
        status = 'Passed' if self.did_succeed else 'Failed'
        color = 'good' if self.did_succeed else 'danger'
        self.post_result('E2E Full Run Ended', status, "Total Duration: {}".format(str(elapsed_time)), color=color)
        self.mark_time(E2E_ENDED, self.get_time_now())

    @staticmethod
    def on_iter(iter_num):
        logger.info('%s  STARTING ITERATION # %s  %s', '-' * 20, iter_num, '-' * 20)

    def print_status(self):
        for test_obj in self.all_tests:
            if test_obj.status['e2e_status']['status'] != SUCCESS_STATUS:
                status = json.dumps(test_obj.status, indent=4)
            else:
                status = test_obj.status['e2e_status']['status']
            logger.info('{date} {run_id} {scenario_name} ==> status: {status}'.format(
                date=str(self.get_time_now()),
                run_id=test_obj.run_id,
                scenario_name=test_obj.scenario_name,
                status=status))

    def run_all(self):
        self.start_time = self.get_time_now()
        self.mark_time(E2E_START_LABEL, self.start_time)
        # run all tests in parallel
        logger.info('Going to run the following tests: %s', TESTS_TO_EXECUTE)
        for test in TESTS_TO_EXECUTE:
            test_obj = E2eTest(self.token, self.cluster, self.domain)
            result = test_obj.run(test)
            if result:
                logger.info('The test: %s was added successfully', test)
                self.all_tests.append(test_obj)
            else:
                logger.error(f'Failed to add {test=}, {result=}')
                return self.did_succeed

            time.sleep(5)
        self.status_loop()
        return self.did_succeed

    def status_loop(self):
        # check status
        i = 0
        break_all = False
        while i < NUM_OF_WAIT_ITER:
            self.on_iter(i + 1)
            for test_obj in self.all_tests:
                test_obj.get_status()
            all_passed = True
            for test_obj in self.all_tests:
                all_passed = all_passed and (test_obj.status['e2e_status']['status'] == SUCCESS_STATUS)
                if test_obj.status['e2e_status']['status'] == FAILED_STATUS:
                    logger.error('Failure has been found')
                    self.print_status()
                    # will break the while
                    break_all = True
                    # break the for
                    break
            # break the while
            if break_all:
                logger.error('*** FAILURE DETECTED - BREAKING ***')
                break

            self.print_status()
            if all_passed:
                logger.info('*** ALL TESTS PASSED ***')
                self.did_succeed = True
                # break the while
                break
            else:
                logger.info('Sleeping for {} seconds...'.format(SLEEP_TIME))
                time.sleep(SLEEP_TIME)
            i += 1
        if i == NUM_OF_WAIT_ITER:
            logger.info('*** RUN FAILURE - TIMEOUT ***')

        self.on_end()

    def post_result(self, attachment_title, attachment_description, attachment_content, color='good'):

        slack_attachment = dict(pretext='', author_name=self.cluster, text=None)
        slack_attachment_field = dict(title=None, value=None, short=False)

        field = dict(slack_attachment_field)
        attachment = dict(slack_attachment)

        field['title'] = attachment_title
        field['value'] = attachment_description

        attachment['fields'] = []
        attachment['fields'].append(field)
        attachment['text'] = attachment_content
        attachment['color'] = color

        attachments = [attachment]
        slack_attachments = {"attachments": attachments}
        logger.info('Posting result to slack...')
        response = requests.post(SLACK_HOOK, data=json.dumps(slack_attachments),
                                 headers={'Content-type': 'application/json'})
        return response.status_code


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='E2E Tests')
    parser.add_argument('token',
                        help='e2e token')
    parser.add_argument('cluster',
                        help='cluster to run tests against')
    parser.add_argument('domain', nargs='?',
                        help='domain name sitcsec.com/cloudlockng.com',
                        default='cloudlockng.com')
    args = parser.parse_args()
    tests_obj = E2eTests(args.token, args.cluster, args.domain)
    did_succeed = tests_obj.run_all()
    if not did_succeed:
        raise RuntimeError(E2E_RUN_FAILED)
