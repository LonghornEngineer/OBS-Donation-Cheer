import threading as th
import logging
import os
import sys
import time
import pickle
import config
import requests
from obswebsocket import events as ows_events
from obswebsocket import requests as ows_requests
from obswebsocket import obsws
from pynput.keyboard import Key, Listener

import openai
import elevenlabs

CFG = config.Config('config.cfg')
API_WAIT_TIME = CFG['api_wait_time']
DONATION_WAIT_TIME = CFG['donation_wait_time']
EXTRALIFE_API_URL = CFG['extralife_api_url']
PARTICIPANT_ID = CFG['participant_id']

openai.api_key = CFG['open_ai_api_key']
CHAT_GPT_PROMPT = CFG['chat_gpt_prompt']
CHAT_GPT_MODEL = CFG['chat_gpt_model']

elevenlabs.set_api_key(CFG['elevenlabs_api_key'])

OBS_HOST = CFG['obs_host']
OBS_PORT = CFG['obs_port']
OBS_PASSWORD = CFG['obs_password']
OBS_WEBSOCKET = obsws(OBS_HOST, OBS_PORT, OBS_PASSWORD)

NEW_DONATIONS = []

GOAL = 0
CURRENT_TOTAL = 0

DEFAULT_DISPLAY_NAME = 'Anonymous'
DEFAULT_AMOUNT = 0

PAGE_UP_PRESSED = False
PAGE_DOWN_PRESSED = False


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

                if 'message' in donation:
                    message = donation['message']
                else:
                    message = ''

                payload = {
                    'displayName': name,
                    'amount': amount,
                    'message': message
                }

                lck.acquire()
                NEW_DONATIONS.append(payload)
                lck.release()

        id_file = open(id_file_name, 'wb')
        pickle.dump(previous_donation_ids, id_file)
        id_file.close()

    restart_thread(wait_time, check_for_new_donations, (url, wait_time, id_file_name, lck))

    return


def check_donation_queue(wait_time, lck):
    global NEW_DONATIONS
    global CHAT_GPT_PROMPT
    global CHAT_GPT_MODEL

    logging.debug('Check Donation Queue.')

    lck.acquire()

    if len(NEW_DONATIONS) > 0:
        logging.debug('New Donation to process!')

        donation = NEW_DONATIONS.pop()
        lck.release()

        payload = donation['displayName'] + ' ' + '${:,.2f}'.format(donation['amount'])
        logging.debug('Payload to gpt: {}'.format(payload))

        completion = openai.ChatCompletion.create(
            model=CHAT_GPT_MODEL,
            frequency_penalty=1.0,
            messages=[
                {"role": "system", "content": CHAT_GPT_PROMPT},
                {"role": "user", "content": payload},
            ]
        )

        gpt_response = completion['choices'][0]['message']['content']
        logging.debug('GPT Response: {}'.format(gpt_response))

        audio = elevenlabs.generate(
            text= gpt_response,
            voice=elevenlabs.Voice(
                # voice_id='h9jexKq5xLgpoFBRfx0j',
                voice_id='G9gCBHuxwtu5RygeRMwE',
                settings=elevenlabs.VoiceSettings(stability=0.50, similarity_boost=1.0, style=0.0, use_speaker_boost=False)
            )
        )

        # Start thread for handling Vader to OBS
        vader_thread_on = th.Thread(target=donate_message_on, args=(donation, ))
        vader_thread_on.start()

        elevenlabs.play(audio)

        vader_thread_off = th.Thread(target=donate_message_off)
        vader_thread_off.start()

        if donation['message'] and '#badgelife' in donation['message']:
            update_badgelife(donation['amount'])

    else:
        logging.debug('No Donation to process.')
        lck.release()

    restart_thread(wait_time, check_donation_queue, (wait_time, lck))

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


def donate_message_on(donation):
    global OBS_WEBSOCKET

    logging.debug('Run the Donate Message with payload: {}'.format(donation))

    # Customize this to move items in OSB

    OBS_WEBSOCKET.call(ows_requests.SetTextGDIPlusProperties('Name Text', text=donation['displayName']))
    OBS_WEBSOCKET.call(ows_requests.SetTextGDIPlusProperties('Value Text', text='${:,.2f}'.format(donation['amount'])))
    OBS_WEBSOCKET.call(ows_requests.SetSceneItemProperties('Vader', visible=True))
    time.sleep(1.5)
    OBS_WEBSOCKET.call(ows_requests.SetSceneItemProperties('Donation Group', visible=True))

    return


def donate_message_off():
    global OBS_WEBSOCKET

    OBS_WEBSOCKET.call(ows_requests.SetSceneItemProperties('Vader', visible=False))
    OBS_WEBSOCKET.call(ows_requests.SetSceneItemProperties('Donation Group', visible=False))

    return


def update_deaths(value):
    global OBS_WEBSOCKET

    logging.debug('Update the death counter by: {}'.format(value))

    current_text = OBS_WEBSOCKET.call(ows_requests.GetTextGDIPlusProperties('Deaths')).datain['text']
    logging.debug('Current Text is: {}'.format(current_text))

    current_deaths = int(current_text.split(' ')[1])

    payload = 'DEATHS: ' + str('{:02d}'.format(current_deaths + value))

    OBS_WEBSOCKET.call(ows_requests.SetTextGDIPlusProperties('Deaths', text=payload))

    return


def update_badgelife(new_value):
    global OBS_WEBSOCKET

    logging.debug('Check Badgelife with: {}'.format(new_value))

    current_text = OBS_WEBSOCKET.call(ows_requests.GetTextGDIPlusProperties('badgelife')).datain['text']
    logging.debug('Current Text is: {}'.format(current_text))

    current_amount = float(current_text.split('$')[1])

    if new_value > current_amount:
        payload = '#BADGELIFE TOP SCORE: ' + '${:,.2f}'.format(new_value)

        OBS_WEBSOCKET.call(ows_requests.SetTextGDIPlusProperties('badgelife', text=payload))

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


def on_press(key):
    global PAGE_UP_PRESSED
    global PAGE_DOWN_PRESSED

    # print('{0} pressed'.format(key))
    if key == Key.page_up and PAGE_UP_PRESSED is False:
        PAGE_UP_PRESSED = True
        update_deaths(1)

    if key == Key.page_down and PAGE_DOWN_PRESSED is False:
        PAGE_DOWN_PRESSED = True
        update_deaths(-1)

    return True


def on_release(key):
    global PAGE_UP_PRESSED
    global PAGE_DOWN_PRESSED

    # print('{0} release'.format(key))
    if key == Key.page_up:
        PAGE_UP_PRESSED = False

    if key == Key.page_down:
        PAGE_DOWN_PRESSED = False

    return True


def main():
    global DONATION_WAIT_TIME
    global API_WAIT_TIME

    global EXTRALIFE_API_URL
    global PARTICIPANT_ID

    global OBS_HOST
    global OBS_PORT
    global OBS_PASSWORD
    global OBS_WEBSOCKET

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s', handlers=[logging.FileHandler('file.log'), logging.StreamHandler()])

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

    t2 = th.Timer(1, check_donation_queue, (DONATION_WAIT_TIME, lock1))
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


    with Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()

    return


if __name__ == '__main__':
    main()