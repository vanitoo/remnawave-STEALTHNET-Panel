"""
Модуль для генерации изображений тарифов
"""
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import re


def remove_emoji(text):
    """Удаляет emoji из текста, так как PIL их не поддерживает"""
    if not text:
        return text
    
    # Удаляем emoji (Unicode диапазоны для emoji)
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001F900-\U0001F9FF"  # supplemental symbols and pictographs
        "\U0001FA00-\U0001FA6F"  # chess symbols
        "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-A
        "]+",
        flags=re.UNICODE
    )
    return emoji_pattern.sub('', text).strip()


def clean_text_for_image(text):
    """Очищает текст для отображения на изображении - убирает emoji и форматирование"""
    if not text:
        return text
    
    # Удаляем emoji
    text = remove_emoji(text)
    
    # Убираем markdown разметку
    text = text.replace('**', '').replace('__', '').replace('*', '').replace('_', '')
    
    return text

# Загружаем шрифты с поддержкой кириллицы (DejaVu Sans)
# Пробуем разные пути, где могут быть установлены шрифты
def load_font(path, size):
    """Загружает шрифт, пробуя разные пути"""
    paths = [
        path,
        path.replace("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/TTF/"),
    ]
    for font_path in paths:
        try:
            return ImageFont.truetype(font_path, size=size)
        except:
            continue
    return ImageFont.load_default()

# Загружаем шрифты
try:
    FONT_BOLD = load_font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
    FONT_REGULAR = load_font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
    FONT_MEDIUM = load_font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
    FONT_SMALL = load_font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    FONT_TINY = load_font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
except:
    # Fallback на стандартный шрифт (не поддерживает кириллицу, но лучше чем ошибка)
    FONT_BOLD = ImageFont.load_default()
    FONT_REGULAR = ImageFont.load_default()
    FONT_MEDIUM = ImageFont.load_default()
    FONT_SMALL = ImageFont.load_default()
    FONT_TINY = ImageFont.load_default()


def draw_rounded_rectangle(draw, xy, radius, fill=None, outline=None, width=1):
    """Рисует прямоугольник со скругленными углами"""
    x1, y1, x2, y2 = xy
    
    # Рисуем углы
    draw.ellipse([x1, y1, x1 + radius*2, y1 + radius*2], fill=fill, outline=outline, width=width)
    draw.ellipse([x2 - radius*2, y1, x2, y1 + radius*2], fill=fill, outline=outline, width=width)
    draw.ellipse([x1, y2 - radius*2, x1 + radius*2, y2], fill=fill, outline=outline, width=width)
    draw.ellipse([x2 - radius*2, y2 - radius*2, x2, y2], fill=fill, outline=outline, width=width)
    
    # Рисуем стороны
    draw.rectangle([x1 + radius, y1, x2 - radius, y1 + radius*2], fill=fill, outline=None)
    draw.rectangle([x1 + radius, y2 - radius*2, x2 - radius, y2], fill=fill, outline=None)
    draw.rectangle([x1, y1 + radius, x1 + radius*2, y2 - radius], fill=fill, outline=None)
    draw.rectangle([x2 - radius*2, y1 + radius, x2, y2 - radius], fill=fill, outline=None)
    
    # Центральная часть
    draw.rectangle([x1 + radius, y1 + radius, x2 - radius, y2 - radius], fill=fill, outline=None)
    
    # Обводка
    if outline:
        # Верхняя и нижняя линии
        draw.line([(x1 + radius, y1), (x2 - radius, y1)], fill=outline, width=width)
        draw.line([(x1 + radius, y2), (x2 - radius, y2)], fill=outline, width=width)
        # Боковые линии
        draw.line([(x1, y1 + radius), (x1, y2 - radius)], fill=outline, width=width)
        draw.line([(x2, y1 + radius), (x2, y2 - radius)], fill=outline, width=width)


def draw_shadow(img, draw, xy, radius, shadow_color=(0, 0, 0), shadow_opacity=20, blur_size=8):
    """Рисует тень для скругленного прямоугольника"""
    x1, y1, x2, y2 = xy
    
    # Создаем временное изображение для тени
    shadow_img = Image.new('RGBA', (img.width, img.height), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_img)
    
    # Рисуем тень большего размера
    offset = blur_size // 2
    shadow_x1 = x1 + offset
    shadow_y1 = y1 + offset
    shadow_x2 = x2 + offset
    shadow_y2 = y2 + offset
    
    # Рисуем скругленный прямоугольник для тени
    shadow_fill = (*shadow_color, shadow_opacity)
    draw_rounded_rectangle(shadow_draw, (shadow_x1, shadow_y1, shadow_x2, shadow_y2), 
                          radius, fill=shadow_fill, outline=None)
    
    # Применяем размытие (простая имитация через несколько слоев)
    for i in range(blur_size):
        alpha = shadow_opacity // (blur_size + 1) * (blur_size - i)
        if alpha > 0:
            temp_img = Image.new('RGBA', (img.width, img.height), (0, 0, 0, 0))
            temp_draw = ImageDraw.Draw(temp_img)
            offset2 = i
            draw_rounded_rectangle(temp_draw, 
                                  (shadow_x1 + offset2, shadow_y1 + offset2, 
                                   shadow_x2 + offset2, shadow_y2 + offset2),
                                  radius, fill=(*shadow_color, alpha), outline=None)
            shadow_img = Image.alpha_composite(shadow_img, temp_img)
    
    # Накладываем тень на основное изображение
    img.paste(shadow_img, (0, 0), shadow_img)
    # Восстанавливаем draw после изменения изображения
    draw = ImageDraw.Draw(img)
    return draw


def generate_tariff_image(
    tier_name: str,
    tier_icon: str,
    features: list,
    tariffs: list,
    currency: str,
    currency_symbol: str,
    primary_color: tuple = (63, 105, 255)  # Синий цвет по умолчанию #3f69ff
) -> bytes:
    """
    Генерирует красивое изображение тарифа
    
    Args:
        tier_name: Название тарифа (например, "Базовый")
        tier_icon: Иконка тарифа (например, "📦")
        features: Список функций тарифа
        tariffs: Список тарифов с ценами
        currency: Код валюты (uah, rub, usd)
        currency_symbol: Символ валюты (₴, ₽, $)
        primary_color: Основной цвет (RGB tuple)
    
    Returns:
        bytes: PNG изображение в виде bytes
    """
    # Размеры изображения
    width = 1400
    padding = 60
    card_padding = 40
    card_spacing = 25
    corner_radius = 24
    
    # Цвета - улучшенная палитра
    bg_color = (248, 250, 252)  # #f8fafc - светлый фон
    bg_gradient_start = (238, 242, 250)  # Более насыщенный градиент
    card_bg = (255, 255, 255)  # Белый фон карточек
    text_primary = (15, 23, 42)  # #0f172a - темный текст
    text_secondary = (100, 116, 139)  # #64748b - серый текст
    border_color = (226, 232, 240)  # #e2e8f0 - граница
    shadow_color = (0, 0, 0)  # Цвет тени
    accent_light = tuple(min(255, c + 50) for c in primary_color)  # Светлый акцент
    accent_dark = tuple(max(0, c - 30) for c in primary_color)  # Темный акцент
    
    # Рассчитываем высоту
    header_height = 160
    features_section_height = (len(features) * 60 + 120) if features else 0
    # Высота таблицы: заголовок + строки (75px каждая) + отступы
    tariffs_table_height = 60 + len(tariffs) * 75 + 80  # Заголовок + строки + отступы
    total_height = header_height + features_section_height + tariffs_table_height + padding * 2 + card_spacing * 3
    
    # Создаем изображение с градиентным фоном
    img = Image.new('RGB', (width, total_height), color=bg_color)
    draw = ImageDraw.Draw(img)
    
    # Рисуем улучшенный градиентный фон (более плавный и выразительный)
    for i in range(total_height):
        # Двойной градиент для более красивого эффекта
        progress = i / total_height
        # Первый градиент (сверху)
        alpha1 = min(0.12, progress * 0.12)
        # Второй градиент (снизу, обратный)
        alpha2 = min(0.08, (1 - progress) * 0.08)
        alpha = alpha1 + alpha2
        
        color = tuple(
            int(bg_color[j] * (1 - alpha) + primary_color[j] * alpha)
            for j in range(3)
        )
        draw.line([(0, i), (width, i)], fill=color)
    
    y = padding
    
    # ========== ЗАГОЛОВОК ==========
    header_card_height = 120
    header_y = y
    
    # Рисуем тень для заголовка (улучшенная)
    shadow_offset = 6
    shadow_blur = 12
    for i in range(shadow_blur):
        alpha = 8 * (1 - i / shadow_blur)
        shadow_r = int(shadow_color[0] * alpha / 255)
        shadow_g = int(shadow_color[1] * alpha / 255)
        shadow_b = int(shadow_color[2] * alpha / 255)
        shadow_fill = (shadow_r, shadow_g, shadow_b)
        draw_rounded_rectangle(
            draw,
            (padding + shadow_offset + i, header_y + shadow_offset + i,
             width - padding + shadow_offset + i, header_y + header_card_height + shadow_offset + i),
            radius=corner_radius,
            fill=shadow_fill,
            outline=None
        )
    
    # Рисуем карточку заголовка с градиентом
    draw_rounded_rectangle(
        draw,
        (padding, header_y, width - padding, header_y + header_card_height),
        radius=corner_radius,
        fill=card_bg,
        outline=border_color,
        width=1
    )
    
    # Добавляем легкий градиент в заголовке (опционально, через небольшой оверлей)
    # Для простоты оставляем белый фон, но можно добавить легкий градиент
    
    # Текст заголовка (убираем emoji) с улучшенной типографикой
    header_text = clean_text_for_image(tier_name)
    bbox = draw.textbbox((0, 0), header_text, font=FONT_BOLD)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (width - text_width) // 2
    y_text = header_y + (header_card_height - text_height) // 2
    
    # Добавляем легкую тень для текста (для глубины)
    draw.text((x + 2, y_text + 2), header_text, fill=(primary_color[0] // 3, primary_color[1] // 3, primary_color[2] // 3), font=FONT_BOLD)
    # Основной текст
    draw.text((x, y_text), header_text, fill=primary_color, font=FONT_BOLD)
    
    y += header_card_height + card_spacing
    
    # ========== ФУНКЦИИ ТАРИФА ==========
    if features:
        features_card_height = len(features) * 60 + 100
        features_y = y
        
        # Тень для карточки функций
        shadow_offset = 4
        shadow_blur = 10
        for i in range(shadow_blur):
            alpha = 6 * (1 - i / shadow_blur)
            shadow_r = int(shadow_color[0] * alpha / 255)
            shadow_g = int(shadow_color[1] * alpha / 255)
            shadow_b = int(shadow_color[2] * alpha / 255)
            shadow_fill = (shadow_r, shadow_g, shadow_b)
            draw_rounded_rectangle(
                draw,
                (padding + shadow_offset + i, features_y + shadow_offset + i,
                 width - padding + shadow_offset + i, features_y + features_card_height + shadow_offset + i),
                radius=corner_radius,
                fill=shadow_fill,
                outline=None
            )
        
        draw_rounded_rectangle(
            draw,
            (padding, features_y, width - padding, features_y + features_card_height),
            radius=corner_radius,
            fill=card_bg,
            outline=border_color,
            width=1
        )
        
        # Заголовок секции функций с улучшенной типографикой
        features_label = "Включено в тариф:"
        draw.text((padding + card_padding, features_y + card_padding), features_label, 
                 fill=text_primary, font=FONT_MEDIUM)
        
        feature_y = features_y + card_padding + 55
        
        for i, feature in enumerate(features[:5]):
            if isinstance(feature, dict):
                feature_name = feature.get("name") or feature.get("title") or feature.get("key", "")
                icon = feature.get("icon", "✓")
            elif isinstance(feature, str):
                feature_name = feature
                icon = "✓"
            else:
                continue
            
            if not feature_name:
                continue
            
            # Иконка в цветном круге (улучшенный дизайн)
            icon_x = padding + card_padding
            icon_y = feature_y
            icon_size = 36
            icon_radius = icon_size // 2
            
            # Фон иконки с градиентом (имитация через два круга)
            icon_bg_light = tuple(min(255, int(c * 0.2 + 20)) for c in primary_color)
            icon_bg_dark = tuple(int(c * 0.12) for c in primary_color)
            
            # Рисуем круг с градиентом (имитация)
            draw.ellipse(
                [icon_x, icon_y, icon_x + icon_size, icon_y + icon_size],
                fill=icon_bg_dark,
                outline=tuple(int(c * 0.25) for c in primary_color),
                width=1
            )
            
            # Иконка текстом (используем галочку, emoji не поддерживаются)
            icon_text = "✓"
            icon_bbox = draw.textbbox((0, 0), icon_text, font=FONT_SMALL)
            icon_text_width = icon_bbox[2] - icon_bbox[0]
            icon_text_height = icon_bbox[3] - icon_bbox[1]
            # Тень для иконки
            draw.text(
                (icon_x + (icon_size - icon_text_width) // 2 + 1, icon_y + (icon_size - icon_text_height) // 2 + 1),
                icon_text,
                fill=(primary_color[0] // 2, primary_color[1] // 2, primary_color[2] // 2),
                font=FONT_SMALL
            )
            # Основная иконка
            draw.text(
                (icon_x + (icon_size - icon_text_width) // 2, icon_y + (icon_size - icon_text_height) // 2),
                icon_text,
                fill=primary_color,
                font=FONT_SMALL
            )
            
            # Текст функции (очищаем от emoji) с улучшенной типографикой
            feature_text = clean_text_for_image(feature_name)
            draw.text((icon_x + icon_size + 18, feature_y + 8), feature_text, 
                     fill=text_primary, font=FONT_SMALL)
            
            feature_y += 60
        
        if len(features) > 5:
            more_text = f"... и еще {len(features) - 5} функций"
            draw.text((padding + card_padding + 45, feature_y), more_text, 
                     fill=text_secondary, font=FONT_TINY)
        
        y += features_card_height + card_spacing
    
    # ========== ТАБЛИЦА ТАРИФОВ ==========
    duration_label = "Выберите длительность:"
    label_y = y
    draw.text((padding, label_y), duration_label, fill=text_primary, font=FONT_MEDIUM)
    y += 65
    
    # Заголовок таблицы (современный дизайн с хорошей читаемостью)
    table_header_y = y
    table_header_height = 55
    
    # Современный градиент - более тонкий и плавный
    header_color_base = primary_color
    # Делаем цвет немного темнее для лучшего контраста с белым текстом
    header_color_dark = tuple(max(0, int(c * 0.85)) for c in primary_color)
    header_color_light = tuple(min(255, int(c * 1.1)) for c in primary_color)
    
    # Рисуем скругленный прямоугольник с градиентом
    # Используем средний цвет для основного фона, чтобы избежать проблем с углами
    header_color_avg = tuple(
        int((header_color_dark[j] + header_color_light[j]) / 2)
        for j in range(3)
    )
    
    # Рисуем основной скругленный прямоугольник
    draw_rounded_rectangle(
        draw,
        (padding, table_header_y, width - padding, table_header_y + table_header_height),
        radius=corner_radius,
        fill=header_color_avg,
        outline=None
    )
    
    # Рисуем градиент поверх (только для центральной части, избегая углов)
    gradient_steps = 8
    inner_padding = corner_radius  # Отступ от углов
    for i in range(gradient_steps):
        # Градиент от темного к светлому
        progress = i / (gradient_steps - 1)
        step_color = tuple(
            int(header_color_dark[j] * (1 - progress) + header_color_light[j] * progress)
            for j in range(3)
        )
        step_height = max(1, table_header_height // gradient_steps)
        step_y = table_header_y + i * step_height
        if i == gradient_steps - 1:
            # Последний шаг - до конца
            step_height = table_header_y + table_header_height - step_y
        
        # Рисуем только центральную часть (избегая углов)
        draw.rectangle(
            [padding + inner_padding, step_y, width - padding - inner_padding, step_y + step_height],
            fill=step_color,
            outline=None
        )
    
    # Легкая нижняя граница для разделения
    draw.line(
        [(padding, table_header_y + table_header_height), (width - padding, table_header_y + table_header_height)],
        fill=tuple(int(c * 0.7) for c in primary_color),
        width=1
    )
    
    # Колонки заголовка (современная типографика)
    col_widths = [300, 200, 220, 200, 200]  # Название, Цена, Цена/день, Длительность, Пустое
    col_labels = ["Тариф", "Цена", "Цена/день", "Длительность", ""]
    col_x = padding + card_padding
    
    for i, (label, col_w) in enumerate(zip(col_labels, col_widths)):
        if label:
            label_bbox = draw.textbbox((0, 0), label, font=FONT_MEDIUM)
            label_height = label_bbox[3] - label_bbox[1]
            label_x = col_x
            label_y_pos = table_header_y + (table_header_height - label_height) // 2
            
            # Белый текст без тени (чистый текст)
            draw.text(
                (label_x, label_y_pos),
                label,
                fill=(255, 255, 255),
                font=FONT_MEDIUM
            )
        col_x += col_w
    
    y += table_header_height + 12
    
    # Общая карточка для всех строк тарифов (единая таблица)
    total_rows = len(tariffs)
    table_rows_height = total_rows * 75
    table_bottom_y = y + table_rows_height
    
    # Рисуем общую карточку для таблицы
    draw_rounded_rectangle(
        draw,
        (padding, y, width - padding, table_bottom_y),
        radius=corner_radius,
        fill=card_bg,
        outline=border_color,
        width=1
    )
    
    # Строки тарифов (без отдельных карточек, просто разделители)
    for idx, tariff in enumerate(tariffs):
        name_raw = tariff.get("name", f"{tariff.get('duration_days', 0)} дней")
        name = clean_text_for_image(name_raw)  # Очищаем от emoji
        price_field_map = {
            "uah": "price_uah",
            "rub": "price_rub",
            "usd": "price_usd"
        }
        price_field = price_field_map.get(currency, "price_uah")
        price = tariff.get(price_field, 0)
        duration = tariff.get("duration_days", 0)
        per_day = price / duration if duration > 0 else price
        
        row_height = 75
        row_y = y
        
        # Легкий фон для четных строк (опционально, можно убрать)
        if idx % 2 == 1:
            row_bg = (250, 252, 255)
            draw.rectangle(
                [padding, row_y, width - padding, row_y + row_height],
                fill=row_bg,
                outline=None
            )
        
        # Разделитель между строками (кроме последней)
        if idx < total_rows - 1:
            divider_y = row_y + row_height
            draw.line(
                [(padding + card_padding, divider_y), (width - padding - card_padding, divider_y)],
                fill=border_color,
                width=1
            )
        
        # Данные в колонках
        col_x = padding + card_padding
        
        # Колонка 1: Название
        name_text = name
        name_bbox = draw.textbbox((0, 0), name_text, font=FONT_SMALL)
        name_height = name_bbox[3] - name_bbox[1]
        draw.text(
            (col_x, row_y + (row_height - name_height) // 2),
            name_text,
            fill=text_primary,
            font=FONT_SMALL
        )
        col_x += col_widths[0]
        
        # Колонка 2: Цена
        price_text = f"{price:.0f} {currency_symbol}"
        price_bbox = draw.textbbox((0, 0), price_text, font=FONT_SMALL)
        price_height = price_bbox[3] - price_bbox[1]
        draw.text(
            (col_x, row_y + (row_height - price_height) // 2),
            price_text,
            fill=primary_color,
            font=FONT_SMALL
        )
        col_x += col_widths[1]
        
        # Колонка 3: Цена за день
        per_day_text = f"{per_day:.2f} {currency_symbol}/день"
        per_day_bbox = draw.textbbox((0, 0), per_day_text, font=FONT_TINY)
        per_day_height = per_day_bbox[3] - per_day_bbox[1]
        draw.text(
            (col_x, row_y + (row_height - per_day_height) // 2),
            per_day_text,
            fill=text_secondary,
            font=FONT_TINY
        )
        col_x += col_widths[2]
        
        # Колонка 4: Длительность
        duration_text = f"{duration} дней"
        duration_bbox = draw.textbbox((0, 0), duration_text, font=FONT_TINY)
        duration_height = duration_bbox[3] - duration_bbox[1]
        draw.text(
            (col_x, row_y + (row_height - duration_height) // 2),
            duration_text,
            fill=text_secondary,
            font=FONT_TINY
        )
        
        y += row_height
    
    # Сохраняем в bytes
    output = BytesIO()
    img.save(output, format='PNG', optimize=True, quality=95)
    output.seek(0)
    return output.getvalue()
