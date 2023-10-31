import threading as th
import logging
from collections import deque
from playsound import playsound
import os
import sys
import time
import random
import pickle
import config
import requests
from obswebsocket import events as ows_events
from obswebsocket import requests as ows_requests
from obswebsocket import obsws

CFG = config.Config('config.cfg')
API_WAIT_TIME = CFG['api_wait_time']
AUDIO_WAIT_TIME = CFG['audio_wait_time']
EXTRALIFE_API_URL = CFG['extralife_api_url']
PARTICIPANT_ID = CFG['participant_id']
SHUFFLE_SIZE = CFG['size_of_shuffle']

OBS_HOST = CFG['obs_host']
OBS_PORT = CFG['obs_port']
OBS_PASSWORD = CFG['obs_password']
OBS_WEBSOCKET = obsws(OBS_HOST, OBS_PORT, OBS_PASSWORD)

MEDIA_DIRECTORY = CFG['media_directory']

NEW_DONATIONS = []
PREVIOUS_CLIPS = deque(SHUFFLE_SIZE * [0], SHUFFLE_SIZE)

GOAL = 0
CURRENT_TOTAL = 0

DEFAULT_DISPLAY_NAME = 'Anonymous'
DEFAULT_AMOUNT = 0


def on_event(message):
    print(u"Got message: {}".format(message))


def on_switch(message):
    print(u"You changed the scene to {}".format(message.getSceneName()))


def check_web(url):
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        logging.debug('URL: {}\nStatus Code: {}\nContent: {}'.format(url, response.status_code, response.json()))
        return False


def check_id_file(file_name):
    if not (os.path.exists(file_name)):
        logging.debug('File missing. Creating new file: {}'.format(file_name))
        id_file = open(file_name, 'wb')
        data = []
        pickle.dump(data, id_file)
        id_file.close()
    else:
        logging.debug('File {} exists.'.format(file_name))
    return True


def check_for_new_donations(url, wait_time, id_file_name, lck):
    global NEW_DONATIONS
    global DEFAULT_DISPLAY_NAME
    global DEFAULT_AMOUNT

    logging.debug('Check the API for new donations.')

    if os.path.getsize(id_file_name) > 0:
        id_file = open(id_file_name, 'rb')
        previous_donation_ids = pickle.load(id_file)
        id_file.close()
    else:
        previous_donation_ids = []

    donation_url = url + '/donations'
    response = requests.get(donation_url)

    if response.status_code != 200:
        logging.debug('URL: {}\nStatus Code: {}\nContent: {}'.format(donation_url, response.status_code, response.text))

    else:
        results = response.json()

        for donation in results:
            donation_id = donation['donationID']

            if donation_id not in previous_donation_ids:
                logging.debug('New Donation: {}\nContent: {}'.format(donation_id, donation))

                previous_donation_ids.append(donation_id)

                if 'displayName' in donation:
                    name = donation['displayName']
                else:
                    name = DEFAULT_DISPLAY_NAME

                if 'amount' in donation:
                    amount = donation['amount']
                else:
                    amount = DEFAULT_AMOUNT

                payload = {
                    'displayName': name,
                    'amount': amount
                }

                lck.acquire()
                NEW_DONATIONS.append(payload)
                lck.release()

        id_file = open(id_file_name, 'wb')
        pickle.dump(previous_donation_ids, id_file)
        id_file.close()

    restart_thread(wait_time, check_for_new_donations, (url, wait_time, id_file_name, lck))

    return


def check_audio_queue(wait_time, lck):
    global NEW_DONATIONS
    global PREVIOUS_CLIPS

    global MEDIA_DIRECTORY

    logging.debug('Check Audio Queue.')

    lck.acquire()

    if len(NEW_DONATIONS) > 0:
        logging.debug('Play Audio File.')

        donation = NEW_DONATIONS.pop()
        lck.release()

        audio_file_list = os.listdir(MEDIA_DIRECTORY + '/Audio_Clips')
        audio_file_cnt = len(audio_file_list)

        repeat = True
        while repeat:
            rand_num = random.randrange(0, audio_file_cnt)
            audio_file = audio_file_list[rand_num]
            if audio_file in PREVIOUS_CLIPS:
                repeat = True
            else:
                PREVIOUS_CLIPS.pop()
                PREVIOUS_CLIPS.appendleft(audio_file)
                repeat = False

                clip_2_play = MEDIA_DIRECTORY + '/Audio_Clips/' + audio_file
                logging.debug('Clip 2 Play: {}'.format(clip_2_play))

                # # Start thread for handling Duke Message to OBS
                duke_thread = th.Thread(target=donate_message, args=(donation, ))
                duke_thread.start()

                playsound(clip_2_play)
                time.sleep(1)

    else:
        logging.debug('No Audio to play.')
        lck.release()

    restart_thread(wait_time, check_audio_queue, (wait_time, lck))

    return


def check_donation_goals(url, wait_time, lck):
    global GOAL
    global CURRENT_TOTAL

    logging.debug('Check the API for donations goal progress.')

    donation_url = url
    response = requests.get(donation_url)

    if response.status_code != 200:
        logging.debug('URL: {}\nStatus Code: {}\nContent: {}'.format(donation_url, response.status_code, response.text))

    else:
        results = response.json()

        lck.acquire()

        GOAL = results['fundraisingGoal']
        CURRENT_TOTAL = results['sumDonations']

        lck.release()

        update_tracker(GOAL, CURRENT_TOTAL)

    restart_thread(wait_time, check_donation_goals, (url, wait_time, lck))

    return


def restart_thread(wait_time, function, args):
    logging.debug('Restart thread {}\nTimer {}\nArgs {}'.format(function.__name__, wait_time, args))
    new_t = th.Timer(wait_time, function, args)
    new_t.start()
    return


def donate_message(donation):
    global OBS_WEBSOCKET

    logging.debug('Run the Donate Message with payload: {}'.format(donation))

    # Customize this to move items in OSB

    OBS_WEBSOCKET.call(ows_requests.SetTextGDIPlusProperties('Name Text', text=donation['displayName']))
    OBS_WEBSOCKET.call(ows_requests.SetTextGDIPlusProperties('Value Text', text='${:,.2f}'.format(donation['amount'])))
    OBS_WEBSOCKET.call(ows_requests.SetSceneItemProperties('Duke Nukem', visible=True))
    time.sleep(1.5)
    OBS_WEBSOCKET.call(ows_requests.SetSceneItemProperties('Text', visible=True))
    time.sleep(5)
    OBS_WEBSOCKET.call(ows_requests.SetSceneItemProperties('Duke Nukem', visible=False))
    OBS_WEBSOCKET.call(ows_requests.SetSceneItemProperties('Text', visible=False))

    return


def update_tracker(goal, cur_total):
    global OBS_WEBSOCKET

    tmp = (cur_total / goal) * 20

    if tmp > 20:
        bar_builder = '|------OVERKILL------|'

    else:
        bar_cnt = 0
        bar_builder = '|'
        while tmp > 0 and bar_cnt < 20:
            bar_builder = bar_builder + '0'
            bar_cnt = bar_cnt + 1
            tmp = tmp - 1

        while bar_cnt < 20:
            bar_builder = bar_builder + '-'
            bar_cnt = bar_cnt + 1

        bar_builder = bar_builder + '|'

    output = ' {} ${:,.2f}/${:,.0f} '.format(bar_builder, cur_total, goal)

    logging.debug('Update the tracker: {}'.format(output))

    OBS_WEBSOCKET.call(ows_requests.SetTextGDIPlusProperties('Goal', text=output))

    return


def main():
    global AUDIO_WAIT_TIME
    global API_WAIT_TIME

    global EXTRALIFE_API_URL
    global PARTICIPANT_ID

    global OBS_HOST
    global OBS_PORT
    global OBS_PASSWORD
    global OBS_WEBSOCKET

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s', handlers=[logging.FileHandler("file.log"), logging.StreamHandler()])

    extra_life_url = EXTRALIFE_API_URL + PARTICIPANT_ID

    check_web_response = check_web(extra_life_url)
    logging.debug('Check Web Response: {}'.format(check_web_response))

    if check_web_response is False:
        logging.debug('Something wrong with the connection to the API.')
        sys.exit(0)

    if 'eventID' in check_web_response:
        donation_id_file_name = str(check_web_response['eventID']) + '.txt'
    else:
        logging.debug('eventID missing from the API response.')
        sys.exit(0)

    milestone_response = check_web(extra_life_url + '/milestones')
    logging.debug('Milestone Response: {}'.format(milestone_response))

    check_id_file(donation_id_file_name)

    lock1 = th.Lock()
    lock2 = th.Lock()

    t1 = th.Timer(1, check_for_new_donations, (extra_life_url, API_WAIT_TIME, donation_id_file_name, lock1))
    t1.start()

    t2 = th.Timer(1, check_audio_queue, (AUDIO_WAIT_TIME, lock1))
    t2.start()

    t3 = th.Timer(1, check_donation_goals, (extra_life_url, API_WAIT_TIME, lock2))
    t3.start()

    OBS_WEBSOCKET.register(on_event)
    OBS_WEBSOCKET.register(on_switch, ows_events.SwitchScenes)
    OBS_WEBSOCKET.connect()

    scenes = OBS_WEBSOCKET.call(ows_requests.GetSceneList())
    sources = OBS_WEBSOCKET.call(ows_requests.GetSourcesList())

    logging.debug('OBS Scenes Response: {}'.format(scenes))
    logging.debug('OBS Source Response: {}'.format(sources))

    return


if __name__ == "__main__":
    main()