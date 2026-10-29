"""Меню Don Corleone. Данные взяты из сторис Instagram (цены в GEL).

Чтобы поменять цену или добавить позицию, правьте только этот файл.
photo — имя файла в папке photos без расширения.
"""

CATEGORIES = [
    {"id": "tomato", "title": "Пицца на томатном соусе", "emoji": "🍅"},
    {"id": "cream", "title": "Пицца на сливочном соусе", "emoji": "🧀"},
]

# id, категория, название, состав, цена, фото, метка (необязательно)
ITEMS = [
    # Томатный соус
    {"id": "don", "cat": "tomato", "name": "Дон Корлеоне", "price": 23, "photo": "don_corleone", "tag": "Фирменная",
     "desc": "моцарелла, баварские колбаски, маринованное куриное филе, шампиньоны, маслины, луковый кранч"},
    {"id": "margarita", "cat": "tomato", "name": "Маргарита", "price": 17, "photo": "margarita",
     "desc": "моцарелла, томаты, специи, базилик"},
    {"id": "pepperoni", "cat": "tomato", "name": "Пепперони", "price": 20, "photo": "pepperoni",
     "desc": "моцарелла, салями пепперони"},
    {"id": "mona", "cat": "tomato", "name": "Мона Лиза", "price": 21, "photo": "mona_liza",
     "desc": "моцарелла, ветчина, шампиньоны, маслины"},
    {"id": "catalonia", "cat": "tomato", "name": "Каталония", "price": 20, "photo": "catalonia",
     "desc": "моцарелла, колбаски охотничьи, маринованное куриное филе, болгарский перец"},
    {"id": "turin", "cat": "tomato", "name": "Турин", "price": 20, "photo": "turin",
     "desc": "моцарелла, болгарский перец, салями"},
    {"id": "bavarian", "cat": "tomato", "name": "Баварская", "price": 21, "photo": "bavarian",
     "desc": "моцарелла, болгарский перец, колбаски охотничьи, бекон"},
    {"id": "lazio", "cat": "tomato", "name": "Лацио", "price": 21, "photo": "lazio",
     "desc": "моцарелла, горгонзола, томаты, шампиньоны"},
    {"id": "constantia", "cat": "tomato", "name": "Констанция", "price": 28, "photo": "constantia",
     "desc": "моцарелла, горгонзола, бекон, маринованное куриное филе, шампиньоны, зелёный лук"},
    {"id": "venezia", "cat": "tomato", "name": "Венеция", "price": 19, "photo": "venezia",
     "desc": "моцарелла, маринованное куриное филе, шампиньоны, томаты"},
    {"id": "santino", "cat": "tomato", "name": "Сантино", "price": 19, "photo": "santino",
     "desc": "моцарелла, маринованное куриное филе, красный лук, маринованные огурцы"},
    {"id": "frutti", "cat": "tomato", "name": "Фрутти де Маре", "price": 35, "photo": "frutti_di_mare",
     "desc": "чилийская мидия, креветка, кальмар, моцарелла, авокадо"},
    # Сливочный соус
    {"id": "dolce", "cat": "cream", "name": "Дольче Пиканте", "price": 23, "photo": "dolce_piccante",
     "desc": "груша, горгонзола, пармезан, моцарелла, стружка миндаля"},
    {"id": "carmela", "cat": "cream", "name": "Кармэла Корлеоне", "price": 27, "photo": "carmela", "tag": "Сырный борт",
     "desc": "сырный борт, моцарелла, пармезан, бекон, салат айсберг"},
    {"id": "sicilia", "cat": "cream", "name": "Сицилия", "price": 28, "photo": "sicilia",
     "desc": "моцарелла, бекон, куриное филе, салями, ветчина, томаты"},
    {"id": "cheese4", "cat": "cream", "name": "4 сыра", "price": 29, "photo": "four_cheese",
     "desc": "моцарелла, горгонзола, пармезан, камамбер"},
    {"id": "hawaii", "cat": "cream", "name": "Гавайская", "price": 23, "photo": "hawaiian",
     "desc": "моцарелла, маринованное куриное филе, ананас"},
]

ITEMS_BY_ID = {i["id"]: i for i in ITEMS}


def items_in(cat_id):
    return [i for i in ITEMS if i["cat"] == cat_id]
