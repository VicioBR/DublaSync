pkgname=dublasync
pkgver=1.0.0
pkgrel=1
pkgdesc="Aplicativo para sincronização e correção de velocidade de áudio para dublagem"
arch=('any')
url="https://github.com/snwkkj/DublaSync"
license=('LicenseRef-Unknown')
depends=(
    'ffmpeg'
    'pyside6'
    'python'
    'python-numpy'
    'python-scipy'
)
makedepends=('git')
source=("${pkgname}::git+https://github.com/snwkkj/DublaSync.git#commit=4ba84eaffd8237f86451f30caab22d8023b55dbf")
sha256sums=('SKIP')

prepare() {
    cd "$srcdir/$pkgname"

    sed -i \
        's|base_path = os.path.abspath(".")|base_path = os.path.dirname(os.path.abspath(__file__))|' \
        main.py
}

package() {
    cd "$srcdir/$pkgname"

    install -d "$pkgdir/usr/share/dublasync"
    cp -r \
        controllers \
        ui \
        utils \
        workers \
        main.py \
        icone.png \
        "$pkgdir/usr/share/dublasync/"

    install -d "$pkgdir/usr/bin"
    printf '%s\n' \
        '#!/bin/sh' \
        'exec /usr/bin/python /usr/share/dublasync/main.py "$@"' \
        > "$pkgdir/usr/bin/dublasync"
    chmod 755 "$pkgdir/usr/bin/dublasync"

    install -d "$pkgdir/usr/share/applications"
    printf '%s\n' \
        '[Desktop Entry]' \
        'Type=Application' \
        'Name=DublaSync' \
        'Comment=Sincronize faixas de áudio dubladas com uma mídia de referência' \
        'Exec=dublasync' \
        'Icon=dublasync' \
        'Terminal=false' \
        'Categories=AudioVideo;Audio;Video;' \
        'StartupNotify=true' \
        > "$pkgdir/usr/share/applications/dublasync.desktop"

    install -Dm644 \
        icone.png \
        "$pkgdir/usr/share/icons/hicolor/512x512/apps/dublasync.png"

    install -Dm644 \
        README.md \
        "$pkgdir/usr/share/doc/dublasync/README.md"
}
