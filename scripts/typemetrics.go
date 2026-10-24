// typemetrics prints size and cohesion data that gocyclo and gocognit do not give us.
//
// Usage: go run typemetrics.go <dir>
//
// It walks <dir> recursively, skipping *_test.go, zz_generated* and *.pb.go files, and prints
// tab-separated lines:
//
//	func   <file:line>  <receiver type or "-">  <name>  <lines>
//	struct <file:line>  <type name>  <fields>  <packages used by the field types>  <field names>
//	uses   <file:line>  <receiver type>  <name>  <names selected on the receiver>
//
// <lines> counts every line from "func" to the closing brace, including comments and
// blank lines. An embedded field counts as one field, and "a, b int" as two.
// A "uses" line lists every x in "recv.x" inside a method, where recv is the receiver
// variable, so it contains both fields and methods of the type. quality_metrics.py
// separates them with the field names of the struct line and computes TCC (M7) from them.
package main

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

func main() {
	root := os.Args[1]
	fset := token.NewFileSet()
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".go") ||
			strings.HasSuffix(path, "_test.go") || strings.HasPrefix(d.Name(), "zz_generated") ||
			strings.HasSuffix(path, ".pb.go") {
			return err
		}
		file, err := parser.ParseFile(fset, path, nil, parser.SkipObjectResolution)
		if err != nil {
			return err
		}
		for _, decl := range file.Decls {
			switch decl := decl.(type) {
			case *ast.FuncDecl:
				printFunc(fset, decl)
			case *ast.GenDecl:
				for _, spec := range decl.Specs {
					if ts, ok := spec.(*ast.TypeSpec); ok {
						if st, ok := ts.Type.(*ast.StructType); ok {
							printStruct(fset, ts, st)
						}
					}
				}
			}
		}
		return nil
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func printFunc(fset *token.FileSet, f *ast.FuncDecl) {
	recv := "-"
	if f.Recv != nil && len(f.Recv.List) == 1 {
		recv = typeName(f.Recv.List[0].Type)
	}
	start, end := fset.Position(f.Pos()), fset.Position(f.End())
	fmt.Printf("func\t%s:%d\t%s\t%s\t%d\n", start.Filename, start.Line, recv, f.Name.Name, end.Line-start.Line+1)
	if recv != "-" {
		fmt.Printf("uses\t%s:%d\t%s\t%s\t%s\n", start.Filename, start.Line, recv, f.Name.Name,
			strings.Join(receiverSelectors(f), ","))
	}
}

// receiverSelectors returns the sorted names x of every "recv.x" in the body of method f.
func receiverSelectors(f *ast.FuncDecl) []string {
	names := f.Recv.List[0].Names
	if f.Body == nil || len(names) == 0 || names[0].Name == "_" {
		return nil
	}
	recv := names[0].Name
	used := map[string]bool{}
	ast.Inspect(f.Body, func(n ast.Node) bool {
		if sel, ok := n.(*ast.SelectorExpr); ok {
			if id, ok := sel.X.(*ast.Ident); ok && id.Name == recv {
				used[sel.Sel.Name] = true
			}
		}
		return true
	})
	out := make([]string, 0, len(used))
	for name := range used {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}

func printStruct(fset *token.FileSet, ts *ast.TypeSpec, st *ast.StructType) {
	fields := 0
	var fieldNames []string
	pkgs := map[string]bool{}
	for _, field := range st.Fields.List {
		if len(field.Names) == 0 {
			fields++ // embedded field, named after its type
			fieldNames = append(fieldNames, typeName(field.Type))
		} else {
			fields += len(field.Names)
			for _, name := range field.Names {
				fieldNames = append(fieldNames, name.Name)
			}
		}
		// Every "pkg.Type" in the field type means a dependency on package pkg.
		ast.Inspect(field.Type, func(n ast.Node) bool {
			if sel, ok := n.(*ast.SelectorExpr); ok {
				if id, ok := sel.X.(*ast.Ident); ok {
					pkgs[id.Name] = true
				}
			}
			return true
		})
	}
	names := make([]string, 0, len(pkgs))
	for p := range pkgs {
		names = append(names, p)
	}
	sort.Strings(names)
	pos := fset.Position(ts.Pos())
	fmt.Printf("struct\t%s:%d\t%s\t%d\t%s\t%s\n", pos.Filename, pos.Line, ts.Name.Name, fields,
		strings.Join(names, ","), strings.Join(fieldNames, ","))
}

// typeName returns "Kubelet" for both "Kubelet" and "*Kubelet" receivers, and "Mutex" for an
// embedded "sync.Mutex".
func typeName(e ast.Expr) string {
	switch e := e.(type) {
	case *ast.StarExpr:
		return typeName(e.X)
	case *ast.Ident:
		return e.Name
	case *ast.SelectorExpr:
		return e.Sel.Name
	case *ast.IndexExpr: // generic receiver T[K]
		return typeName(e.X)
	case *ast.IndexListExpr:
		return typeName(e.X)
	}
	return "?"
}
