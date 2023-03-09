import json
import os
import time
from flask import Blueprint, request, abort, render_template, current_app, url_for
from flask_login import current_user
from flask_mail import Message
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
import base64
import requests

# from fake_useragent import UserAgent

from app.database import db
from app.database.scan import Scan
from app.mailmodule import mail
from personal_config import domain, executable_path
from app.api.mail_text import report_html


blueprint = Blueprint('api', __name__)


def check_records(vin: str):
    api_response = {}
    url = f'https://api.allreports.tools/wp-json/v1/get_report_check/{vin}'
    r = requests.get(url)
    api_response['api'] = r.json()
    for item in ['iaai', 'manheim', 'copart']:
        url = 'https://reports.checkmyvin.ru/rec_count.php?login=mikecsokass&akey=24ad9ae6825b4acd2ebd3969c1c75234' \
              f'&type={item}' \
              f'&vin={vin}' \
              '&format=json'
        r = requests.get(url)
        api_response[item] = r.json()
    print(api_response)
    return api_response


@blueprint.route('/get_vin', methods=['POST'])
def get_vin():
	url = "https://apibroker-license-plate-search-v1.p.rapidapi.com/license-plate-search"
	querystring = {"format":"json","state":request.form['state'],"plate":request.form['plate']}

	headers = {
		"X-RapidAPI-Key": "09957978a5mshded1a36addba0a6p1bf29ajsnb5f8633e3c82",
		"X-RapidAPI-Host": "apibroker-license-plate-search-v1.p.rapidapi.com"
	}

	r = requests.get(url, headers=headers, params=querystring)
	data = r.json()
	if data.get('status')=="ok":
		return check_records(data['content']['vin'])
	else:
		return "error"


@blueprint.route('/check_by_vin', methods=['POST'])
def check_by_vin():
    record = check_records(request.form['VIN'])
    print(record)
    return record


@blueprint.route('/record/<string:payment_id>', methods=['GET'])
def render_record(payment_id):
    # try:
    scan = Scan.query.filter_by(payment_id=payment_id).first()
    if scan is None or not scan.payed:
        abort(404)
    filename = scan.id
    if scan.done:
        if scan.report_type in ['manheim']:
            swap = ''
            with open(f"{os.getcwd()}/app/templates/scans/{filename}.html", 'r') as file:
                for line in file:
                    swap += line
            return swap
        return render_template(f'scans/{filename}.html')
    if scan.payed:
        if scan.report_type in ['carfax', 'autocheck']:
            report_64 = get_report(scan.vin, scan.report_type)
            decoded_report = base64.b64decode(report_64["report"]["report"])
            utf_report = decoded_report.decode('utf-8')
        elif scan.report_type in ['iaai', 'manheim', 'copart']:
            url = f'https://reports.checkmyvin.ru/?login=mikecsokass&' \
              f'akey=24ad9ae6825b4acd2ebd3969c1c75234&type={scan.report_type}&vin={scan.vin.upper()}'
            r = requests.get(url)
            utf_report = r.text
        i = 0
        'https://reports.checkmyvin.ru/?login=mikecsokass&akey=24ad9ae6825b4acd2ebd3969c1c75234&type=manheim&vin=12345678901234567'
        while True:
            i += 1
            if utf_report.find('<!--') > 0:
                first_part = utf_report[:utf_report.find('<!--')]
                second_part = utf_report[utf_report.find('-->') + 3:]
                utf_report = first_part + second_part
            else:
                break
        if scan.report_type == 'carfax':
            utf_report = add_print_button(utf_report)
        elif scan.report_type == 'manheim':
            utf_report = utf_report.replace('//insightcr.manheim.com/styles/cr-display.min.css?v=186', "/static/css/manheim/cr-display.min.css")
            utf_report = utf_report.replace('//insightcr.manheim.com/styles/mui.min.css?v=186', "/static/css/manheim/mui.min.css")
            utf_report = utf_report.replace('//insightcr.manheim.com/styles/prism-styles-comps.min.css?v=186', "/static/css/manheim/prism-styles-comps.min.css")
            utf_report = utf_report.replace('//insightcr.manheim.com/styles/prism-styles.min.css?v=186', "/static/css/manheim/prism-styles.min.css")
        create_html(filename, utf_report)
        scan.done = True
        db.session.commit()
        file_path = f"{os.getcwd()}/app/templates/scans/{filename}.html"
        if scan.report_type == 'autocheck':
            swap = []
            with open(f"{os.getcwd()}/app/templates/scans/{filename}.html", 'r') as file:
                for line in file:
                    line = format_line(line, looking_for_str='https://')
                    swap.append(line)
            file.close()
            with open(f"{os.getcwd()}/app/templates/scans/AutoCheck_{filename}.html", 'a') as file:
                for line in swap:
                    file.write("\n" + line)
            file.close()
            file_path = f"{os.getcwd()}/app/templates/scans/AutoCheck_{filename}.html"
        address = f"file:///{file_path}"
        result = None
        if scan.report_type in ['carfax', 'autocheck', 'manheim']:
            if scan.report_type in ['manheim', 'autocheck']:
                address = domain + url_for("api.render_record", payment_id=payment_id)[1:]
                print(address)
                # time.sleep(5)
            else:
                result = get_pdf_from_html(address)
        else:
            result = html_to_jpg_to_pdf(address)
        pdf_filename = f'WWW.XPERTS.DO_{scan.report_type.upper()}_{scan.vin}.pdf'
        with open(f"{os.getcwd()}/app/scans/{pdf_filename}", 'wb') as file:
            file.write(result)
        # mail
        if current_user.is_authenticated:
            target_email = current_user.email
        else:
            target_email = scan.payer_email
        msg = Message(f'www.XPerts.do',
                      sender=current_app.config['MAIL_USERNAME'],
                      recipients=[target_email],
                      html=report_html)
        file_path = f'scans/{pdf_filename}'
        with current_app.open_resource(file_path) as fp:
            msg.attach(pdf_filename, "application/pdf", fp.read())
        mail.send(msg)
        if scan.report_type in ['manheim']:
            return utf_report
    return render_template(f'scans/{filename}.html')
    # except Exception as e:
    #     related_string = open('errors.txt', 'r').read()
    #     with open('errors.txt', 'w', encoding='utf-8') as file:
    #         file.write(related_string + '\n' + str(e))
    #     return 'error'


@blueprint.route('/render_page/<string:payment_id>', methods=['GET'])
def render_page(payment_id):
    scan = Scan.query.filter_by(payment_id=payment_id).first()
    filename = scan.id
    swap = ''
    with open(f"{os.getcwd()}/app/templates/scans/{filename}.html", 'r') as file:
        for line in file:
            swap += line
    return render_template(swap)


def get_report(vin: str, report_type: str):
    # Getting report
    api_key = current_app.config['VIN_API_KEY']
    url = f'https://api.allreports.tools/wp-json/v1/get_report_by_wholesaler/{vin.upper()}/{api_key}/{report_type}/en'
    r = requests.get(url)
    return r.json()


def create_html(filename: str, utf_report: str):
    # Creating HTML
    file1 = open(f'app/templates/scans/{filename}.html', "w", encoding="utf-8")
    file1.write(utf_report)
    file1.close()
    return True


def add_print_button(page: str):
    first_part = page[:page.find('class="hdrlogo"')]
    first_part = first_part[:first_part.rfind('<img')]
    second_part = page[page.find('class="hdrlogo"'):]
    second_part = second_part[second_part.find('/>') + 2:]
    print_form = '<form  class="hdrlogo" style="margin-top:0px"><div id="cipPrintBtn" class="open-global-modal ' \
                 'top-bar-button en hdrlogo" data-global-modal="printModal" onclick="window.print()"><i class="' \
                 'material-icons top-bar-button-icon">print</i>Print</div></form>'
    final_string = f'{first_part}{print_form}{second_part}'
    return final_string


def format_line(line, looking_for_str):
    static_dir = './AutoCheck_files'
    if line.find(looking_for_str) > -1:
        first = line[:line.find(looking_for_str)]
        second = line[line.find(looking_for_str):]
        third = second[second.find('"'):]
        second = second[:second.find('"')]
        if second.rfind('/') > 8:
            second = second[second.rfind('/'):]
            line = first + static_dir + second + third
        else:
            return line
    if line.find(looking_for_str) > -1:
        line = format_line(line, looking_for_str)
    return line


# @blueprint.route('/print_pdf')
# def nikus322():
#     filename = '1'
#
#
#     swap = []
#     with open(f"{os.getcwd()}/app/templates/scans/{filename}.html", 'r') as file:
#         for line in file:
#             line = format_line(line, looking_for_str='https://')
#             swap.append(line)
#     file.close()
#     with open(f"{os.getcwd()}/app/templates/scans/AutoCheck_{filename}.html", 'a') as file:
#         for line in swap:
#             file.write("\n" + line)
#     file.close()
#     file_path = f"{os.getcwd()}/app/templates/scans/AutoCheck_{filename}.html"
#
#
#
#     address = f"file:///{file_path}"
#     result = get_pdf_from_html(address)
#     with open(f"{os.getcwd()}/app/scans/{filename}.pdf", 'wb') as file:
#         file.write(result)
#     return 'done'


def html_to_jpg_to_pdf(path):
    webdriver_options = Options()
    webdriver_options.add_argument('--headless')
    webdriver_options.add_argument('--disable-gpu')
    webdriver_options.add_argument('--window-size=1000,1280')
    driver = webdriver.Chrome(executable_path=executable_path, options=webdriver_options)
    driver.set_window_size(1000, 1280)
    print(path)
    driver.get(path)
    driver.save_screenshot('output.png')
    address = f"file:///{os.getcwd()}/output.png"
    driver.get(address)
    calculated_print_options = {
        'landscape': False,
        'displayHeaderFooter': False,
        'printBackground': True,
        'preferCSSPageSize': True,
    }
    calculated_print_options.update({})

    # запускаем печать в pdf файл
    result = send_devtools(driver, "Page.printToPDF", calculated_print_options)
    driver.quit()

    return base64.b64decode(result['data'])


def get_pdf_from_html(path):
    webdriver_options = Options()
    # ua = UserAgent()
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
    print(user_agent)
    webdriver_options.add_argument(f'user-agent={user_agent}')
    webdriver_options.add_argument('--headless')
    webdriver_options.add_argument('--disable-gpu')
    webdriver_options.add_argument('--window-size=1920,1280')
    driver = webdriver.Chrome(executable_path=executable_path, options=webdriver_options)
    driver.set_window_size(1920, 1280)
    # открываем заданный url
    print(path)
    driver.get(path)
    print(2)

    # задаем параметры печати
    calculated_print_options = {
        'landscape': False,
        'displayHeaderFooter': False,
        'printBackground': True,
        'preferCSSPageSize': True,
    }
    print(3)
    calculated_print_options.update({})
    print(4)

    # запускаем печать в pdf файл
    result = send_devtools(driver, "Page.printToPDF", calculated_print_options)
    print(5)
    driver.quit()
    print(6)

    return base64.b64decode(result['data'])


def send_devtools(driver, cmd, params={}):
    resource = "/session/%s/chromium/send_command_and_get_result" % driver.session_id
    driver.save_screenshot('output.png')
    url = driver.command_executor._url + resource
    body = json.dumps({'cmd': cmd, 'params': params})
    response = driver.command_executor._request('POST', url, body)
    # print(response)
    return response.get('value')
