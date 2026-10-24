// importusage lists, for every import of the non-test files of one Go package directory,
// which files import it and which top-level functions of those files use it (P6, Section 2.4).
//
// Usage: go run importusage.go <package dir>
// Output: one line per import: "<import path>\t<number of files>\t<files>\t<functions using it>"
package main

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

func main() {
	dir := os.Args[1]
	fset := token.NewFileSet()
	pkgs, err := parser.ParseDir(fset, dir, func(fi os.FileInfo) bool {
		return !strings.HasSuffix(fi.Name(), "_test.go")
	}, 0)
	if err != nil {
		panic(err)
	}
	files := map[string]map[string]bool{} // import path -> files
	funcs := map[string]map[string]bool{} // import path -> functions that use it
	for _, pkg := range pkgs {
		for name, f := range pkg.Files {
			base := filepath.Base(name)
			local := map[string]string{} // name used in the file -> import path
			for _, imp := range f.Imports {
				path, _ := strconv.Unquote(imp.Path.Value)
				n := path[strings.LastIndex(path, "/")+1:]
				if imp.Name != nil {
					n = imp.Name.Name
				}
				local[n] = path
				if files[path] == nil {
					files[path], funcs[path] = map[string]bool{}, map[string]bool{}
				}
				files[path][base] = true
			}
			// Attribute every use "pkg.Name" to the top-level declaration that contains it.
			for _, decl := range f.Decls {
				owner := "(type/var/const)"
				if fd, ok := decl.(*ast.FuncDecl); ok {
					owner = fd.Name.Name
				}
				ast.Inspect(decl, func(n ast.Node) bool {
					if sel, ok := n.(*ast.SelectorExpr); ok {
						if id, ok := sel.X.(*ast.Ident); ok && id.Obj == nil {
							if path, ok := local[id.Name]; ok {
								funcs[path][base+":"+owner] = true
							}
						}
					}
					return true
				})
			}
		}
	}
	paths := make([]string, 0, len(files))
	for p := range files {
		paths = append(paths, p)
	}
	sort.Strings(paths)
	for _, p := range paths {
		fmt.Printf("%s\t%d\t%s\t%s\n", p, len(files[p]), keys(files[p]), keys(funcs[p]))
	}
}

func keys(m map[string]bool) string {
	k := make([]string, 0, len(m))
	for s := range m {
		k = append(k, s)
	}
	sort.Strings(k)
	return strings.Join(k, ",")
}
