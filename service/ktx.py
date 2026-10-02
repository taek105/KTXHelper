from urllib.parse import urlencode

KORAIL_BASE_URL = "https://www.korail.com"
SEARCH_RESULT_URL = f"{KORAIL_BASE_URL}/ticket/search/list"

# The result route accepts names without codes. Codes are supplied for the
# commonly used KTX stations so the query remains unambiguous.
KTX_STATION_CODES = {
    "행신": "0390",
    "서울": "0001",
    "용산": "0104",
    "수서": "0551",
    "영등포": "0002",
    "광명": "0501",
    "수원": "0003",
    "평택": "0004",
    "천안아산": "0502",
    "천안": "0005",
    "조치원": "0007",
    "오송": "0297",
    "대전": "0010",
    "서대전": "0025",
    "김천구미": "0507",
    "구미": "0013",
    "동대구": "0015",
    "대구": "0023",
    "서대구": "0506",
    "경산": "0024",
    "밀양": "0017",
    "구포": "0019",
    "부산": "0020",
    "경주": "0508",
    "울산(통도사)": "0509",
    "포항": "0515",
    "창원중앙": "0512",
    "마산": "0059",
    "논산": "0027",
    "익산": "0030",
    "정읍": "0033",
    "광주송정": "0036",
    "목포": "0041",
    "전주": "0045",
    "순천": "0051",
    "여수EXPO": "0053",
    "청량리": "0090",
    "강릉": "0115",
    "정동진": "0262",
    "동해": "0113",
}

KTX_STATIONS = [
    "서울",
    "용산",
    "광명",
    "수서",
    "영등포",
    "수원",
    "평택",
    "천안아산",
    "천안",
    "오송",
    "조치원",
    "대전",
    "서대전",
    "김천구미",
    "구미",
    "동대구",
    "대구",
    "경주",
    "울산(통도사)",
    "포항",
    "경산",
    "밀양",
    "부산",
    "구포",
    "창원중앙",
    "평창",
    "진부(오대산)",
    "강릉",
    "익산",
    "전주",
    "광주송정",
    "목포",
    "순천",
    "청량리",
    "여수EXPO",
    "동해",
    "정동진",
    "안동",
    "서원주",
    "원주",
    "마산",
    "행신",
    "나주",
    "정읍",
    "남원",
]


def build_search_url(dpt_stn, arr_stn, date, tm):
    normalized_time = str(tm).replace(":", "")
    if len(normalized_time) == 2:
        normalized_time += "0000"
    elif len(normalized_time) == 4:
        normalized_time += "00"

    params = {
        "txtMenuId": "11",
        "radJobId": "1",
        "searchType": "GENERAL",
        "txtGoStart": dpt_stn,
        "txtGoEnd": arr_stn,
        "txtGoAbrdDt": date,
        "txtGoHour": normalized_time,
        "txtPsgFlg_1": "1",
        "txtPsgFlg_2": "0",
        "txtPsgFlg_3": "0",
        "txtPsgFlg_4": "0",
        "txtPsgFlg_5": "0",
        "txtPsgFlg_8": "0",
        "txtPsgFlg_99": "0",
        "txtTrnGpCd": "100",
        "selGoTrain": "00",
        "selGoSeat1": "015",
        "txtSeatAttCd_4": "015",
        "rtYn": "N",
        "adjStnScdlOfrFlg": "N",
        "adjStnScdlOfrFlg2": "N",
        "srtCheckYn": "N",
        "ebizCrossCheck": "N",
    }
    dpt_code = KTX_STATION_CODES.get(dpt_stn)
    arr_code = KTX_STATION_CODES.get(arr_stn)
    if dpt_code:
        params["txtGoStartCode"] = dpt_code
    if arr_code:
        params["txtGoEndCode"] = arr_code
    return f"{SEARCH_RESULT_URL}?{urlencode(params)}"



from service.ktx_native import KTX, get_schedule
