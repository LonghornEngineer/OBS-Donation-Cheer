import threading as th
from icecream import ic
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

cfg = config.Config('config.cfg')
API_WAIT_TIME = cfg['api_wait_time']
AUDIO_WAIT_TIME = cfg['audio_wait_time']
extralife_api_url = cfg['extralife_api_url']
participant_id = cfg['participant_id']
shuffle_size = cfg['size_of_shuffle']

obs_host = cfg['obs_host']
obs_port = cfg['obs_port']
obs_password = cfg['obs_password']

NEW_DONATIONS = []
PREVIOUS_CLIPS = deque(shuffle_size * [0], shuffle_size)


def on_event(message):
    print(u"Got message: {}".format(message))


def on_switch(message):
    print(u"You changed the scene to {}".format(message.getSceneName()))


def check_web(url):
    response = requests.get(url)
    ic(response)
    if (response.status_code == 200):
        return response.json()
    else:
        return False


def check_id_file(file_name):
    ic(file_name)
    if not (os.path.exists(file_name)):
        ic('File missing. Creating new file.')
        id_file = open(file_name, 'wb')
        data = []
        pickle.dump(data, id_file)
        id_file.close()
    else:
        ic('File Exists.')
    return True


def check_for_new_donations(url, wait_time, id_file_name, lck):
    global NEW_DONATIONS

    ic('Check the API for new donations.')

    if os.path.getsize(id_file_name) > 0:
        id_file = open(id_file_name, 'rb')
        data = pickle.load(id_file)
        ic(data)
        id_file.close()
    else:
        data = []

    donation_url = url + '/donations'
    results = requests.get(donation_url).json()
    ic(results)

    for donation in results:
        ic(donation)
        donationID = donation['donationID']

        if donationID in data:
            ic('Donation ID in data.')
        else:
            ic('New Donation!')
            data.append(donationID)

            if('displayName' in donation):
                name = donation['displayName']
            else:
                name = 'Anonymous'

            payload = {
                'displayName': name,
                'amount': donation['amount']
            }

            lck.acquire()
            NEW_DONATIONS.append(payload)
            lck.release()

    id_file = open(id_file_name, 'wb')
    pickle.dump(data, id_file)
    id_file.close()

    restart_thread(wait_time, check_for_new_donations, (url, wait_time, id_file_name, lck))

    return


def check_audio_queue(wait_time, lck):
    global NEW_DONATIONS
    global PREVIOUS_CLIPS

    lck.acquire()
    ic(NEW_DONATIONS)
    if (len(NEW_DONATIONS) > 0):
        ic('Play Audio File.')
        donation = NEW_DONATIONS.pop()
        lck.release()

        audio_file_list = os.listdir('audio_clips')
        ic(audio_file_list)

        audio_file_cnt = len(audio_file_list)
        ic(audio_file_cnt)

        repeat = True
        while (repeat):
            rand_num = random.randrange(0, audio_file_cnt)
            ic(rand_num)
            audio_file = audio_file_list[rand_num]
            if (audio_file in PREVIOUS_CLIPS):
                repeat = True
            else:
                PREVIOUS_CLIPS.pop()
                PREVIOUS_CLIPS.appendleft(audio_file)
                repeat = False

        clip_2_play = 'audio_clips/' + audio_file
        ic(clip_2_play)

        # # Start thread for handling Duke Message to OBS
        duke_thread = th.Thread(target=duke_message, args=(donation, ))
        duke_thread.start()

        playsound(clip_2_play)
        time.sleep(1)

    else:
        ic('No Audio...')
        lck.release()

    restart_thread(wait_time, check_audio_queue, (wait_time, lck))

    return


def restart_thread(wait_time, function, args):
    ic('Restart thread')
    ic(function.__name__)
    new_t = th.Timer(wait_time, function, args)
    new_t.start()
    return


def duke_message(donation):

    ws_response = ws.call(ows_requests.SetTextGDIPlusProperties('Name Text', text=donation['displayName']))
    ws_response = ws.call(ows_requests.SetTextGDIPlusProperties('Value Text', text='${:,.2f}'.format(donation['amount'])))
    ws_response = ws.call(ows_requests.SetSceneItemProperties('Duke Nukem', visible=True))
    time.sleep(1.5)
    ws_response = ws.call(ows_requests.SetSceneItemProperties('Text', visible=True))
    time.sleep(5)
    ws_response = ws.call(ows_requests.SetSceneItemProperties('Duke Nukem', visible=False))
    ws_response = ws.call(ows_requests.SetSceneItemProperties('Text', visible=False))

    return


EXTRA_LIFE_URL = extralife_api_url + participant_id

about_response = check_web(EXTRA_LIFE_URL)

if about_response is False:
    sys.exit('Something wrong with the connection to the API.')

if 'eventID' in about_response:
    donation_id_file_name = str(about_response['eventID']) + '.txt'
else:
    sys.exit('eventID missing from the API response.')

check_id_file(donation_id_file_name)

lock = th.Lock()

t1 = th.Timer(1, check_for_new_donations, (EXTRA_LIFE_URL, API_WAIT_TIME, donation_id_file_name, lock))
t1.start()

t2 = th.Timer(1, check_audio_queue, (AUDIO_WAIT_TIME, lock))
t2.start()

ws = obsws(obs_host, obs_port, obs_password)
ws.register(on_event)
ws.register(on_switch, ows_events.SwitchScenes)
ws.connect()

scenes = ws.call(ows_requests.GetSceneList())
sources = ws.call(ows_requests.GetSourcesList())

ic(sources)
