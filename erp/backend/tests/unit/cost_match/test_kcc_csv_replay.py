import csv
from pathlib import Path

from openpyxl import Workbook, load_workbook
import pytest

from scripts.kcc_match_bg import run


def test_replay_prices_evidence_not_headers_or_partial_work(tmp_path: Path):
    corpus = tmp_path / 'prices.csv'
    with corpus.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['code', 'description', 'unit', 'rate', 'currency', 'source', 'is_active'])
        writer.writeheader()
        for code, text, unit, rate in [
            ('OPR-brick', 'Иззиждане на отвори с тухла', 'м³', '8'),
            ('OPR-duct', 'Доставка и монтаж на въздуховод ф160', 'м', '20'),
            ('OPR-header', 'СМР-РАЗПРЕДЕЛЕНИЕ КОТА +3.60', 'м2', '999'),
            ('OPR-rampA', 'Бетонна рампа', 'м²', '85'),
            ('OPR-rampB', 'Бетонна рампа', 'кв.м.', '25'),
        ]:
            writer.writerow(dict(code=code, description=text, unit=unit, rate=rate,
                                 currency='EUR', source='operator_pricelist', is_active='True'))
    input_path, output_path = tmp_path / 'input.xlsx', tmp_path / 'output.xlsx'
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['№', 'Описание', 'Ед. мярка', 'Количество'])
    sheet.append([1, 'Иззиждане на отвори с тухла', 'м³', 6])
    sheet.append([2, 'Доставка и монтаж на въздуховод ф160 вкл. фасонни части', 'м', 10])
    sheet.append([3, 'СМР-РАЗПРЕДЕЛЕНИЕ КОТА +3.60', 'м2', 1])
    sheet.append([4, 'Бетонна рампа', 'м²', 1])
    sheet.append([5, 'Ръчно зададена цена', 'бр', 1, 12])
    workbook.save(input_path)
    counts = run(corpus, input_path, output_path, 100)
    priced = load_workbook(output_path, data_only=True)
    assert priced.active['E2'].value == 8
    assert priced.active['F2'].value == 48
    assert priced.active['E3'].value is None
    assert priced.active['E4'].value is None
    assert priced.active['E5'].value is None
    assert priced.active['E6'].value == 12
    assert counts['exact'] == 1
    assert counts['review'] == 2
    assert counts['structural_or_nonwork'] == 1
    assert 'BG_MATCH_AUDIT' in priced.sheetnames
    assert load_workbook(input_path, data_only=True).active['E2'].value is None


def test_replay_refuses_to_overwrite_input(tmp_path):
    source = tmp_path / 'book.xlsx'
    with pytest.raises(ValueError, match='overwrite'):
        run(tmp_path / 'missing.csv', source, source, 100)
